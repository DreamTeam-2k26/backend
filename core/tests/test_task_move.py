from unittest import mock

from rest_framework.test import APIClient, APITestCase

from core.models import Task

from .helpers import client_for, make_project, make_user


def move_url(task):
    return f'/api/tasks/{task.pk}/move/'


class TaskMoveTestCase(APITestCase):
    def setUp(self):
        self.owner = make_user('dono')
        self.member = make_user('membro')
        self.outsider = make_user('externo')
        self.project = make_project(self.owner, members=[self.member])
        self.other_project = make_project(self.outsider, name='Outro projeto')
        self.todo, self.doing, self.done = self.project.columns.order_by('position')
        self.a, self.b, self.c, self.d = (self.make_task(title, self.todo, pos) for pos, title in enumerate('ABCD'))

    def make_task(self, title, column, position):
        return Task.objects.create(project=self.project, column=column, title=title, position=position)

    def move(self, task, column, position, user=None):
        payload = {'column_id': column.pk, 'position': position}
        return client_for(user or self.member).post(move_url(task), payload, format='json')

    def board(self, column):
        return list(column.tasks.order_by('position', 'id').values_list('title', 'position'))


class AccessTests(TaskMoveTestCase):
    def test_requires_authentication(self):
        response = APIClient().post(move_url(self.a), {'column_id': self.doing.pk, 'position': 0})

        self.assertEqual(response.status_code, 401)

    def test_any_member_moves_tasks(self):
        self.assertEqual(self.move(self.a, self.doing, 0, self.member).status_code, 200)

    def test_outsider_gets_404(self):
        response = self.move(self.a, self.doing, 0, self.outsider)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.board(self.todo), [('A', 0), ('B', 1), ('C', 2), ('D', 3)])


class ValidationTests(TaskMoveTestCase):
    def test_column_of_another_project_is_rejected(self):
        response = self.move(self.a, self.other_project.columns.first(), 0)

        self.assertEqual(response.status_code, 400)
        self.assertIn('column_id', response.data)
        self.a.refresh_from_db()
        self.assertEqual(self.a.column, self.todo)

    def test_unknown_column_is_rejected(self):
        response = client_for(self.member).post(move_url(self.a), {'column_id': 999999, 'position': 0}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('column_id', response.data)

    def test_negative_position_is_rejected(self):
        response = self.move(self.a, self.doing, -1)

        self.assertEqual(response.status_code, 400)
        self.assertIn('position', response.data)

    def test_column_and_position_are_required(self):
        response = client_for(self.member).post(move_url(self.a), {}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(set(response.data), {'column_id', 'position'})


class ReorderWithinColumnTests(TaskMoveTestCase):
    def test_move_up(self):
        self.move(self.d, self.todo, 1)

        self.assertEqual(self.board(self.todo), [('A', 0), ('D', 1), ('B', 2), ('C', 3)])

    def test_move_down(self):
        self.move(self.a, self.todo, 2)

        self.assertEqual(self.board(self.todo), [('B', 0), ('C', 1), ('A', 2), ('D', 3)])

    def test_position_past_the_end_goes_last(self):
        self.move(self.b, self.todo, 50)

        self.assertEqual(self.board(self.todo), [('A', 0), ('C', 1), ('D', 2), ('B', 3)])

    def test_messy_positions_are_normalized(self):
        Task.objects.filter(pk__in=[self.a.pk, self.b.pk]).update(position=0)
        Task.objects.filter(pk=self.d.pk).update(position=9)

        self.move(self.c, self.todo, 0)

        self.assertEqual(self.board(self.todo), [('C', 0), ('A', 1), ('B', 2), ('D', 3)])


class MoveAcrossColumnsTests(TaskMoveTestCase):
    def setUp(self):
        super().setUp()
        self.x = self.make_task('X', self.doing, 0)
        self.y = self.make_task('Y', self.doing, 1)

    def test_move_to_another_column_closes_gap_and_opens_slot(self):
        response = self.move(self.b, self.doing, 1)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.board(self.todo), [('A', 0), ('C', 1), ('D', 2)])
        self.assertEqual(self.board(self.doing), [('X', 0), ('B', 1), ('Y', 2)])

    def test_other_columns_are_untouched(self):
        done_task = self.make_task('Feita', self.done, 5)

        self.move(self.b, self.doing, 0)

        done_task.refresh_from_db()
        self.assertEqual(done_task.position, 5)

    def test_response_returns_affected_columns_for_the_board(self):
        response = self.move(self.b, self.doing, 0)

        returned = [(item['title'], item['column'], item['position']) for item in response.data]
        self.assertEqual(
            returned,
            [
                ('A', self.todo.pk, 0),
                ('C', self.todo.pk, 1),
                ('D', self.todo.pk, 2),
                ('B', self.doing.pk, 0),
                ('X', self.doing.pk, 1),
                ('Y', self.doing.pk, 2),
            ],
        )

    def test_entering_done_completes_and_leaving_reopens(self):
        response = self.move(self.a, self.done, 0)
        self.a.refresh_from_db()
        self.assertIsNotNone(self.a.completed_at)
        moved = next(item for item in response.data if item['id'] == self.a.pk)
        self.assertIsNotNone(moved['completed_at'])

        self.move(self.a, self.doing, 0)
        self.a.refresh_from_db()
        self.assertIsNone(self.a.completed_at)

    def test_reordering_inside_done_keeps_completion_date(self):
        self.move(self.a, self.done, 0)
        self.move(self.b, self.done, 0)
        self.a.refresh_from_db()
        first = self.a.completed_at

        self.move(self.a, self.done, 0)
        self.a.refresh_from_db()

        self.assertEqual(self.a.completed_at, first)


class ConcurrencyAndAtomicityTests(TaskMoveTestCase):
    def test_stale_moves_from_two_clients_keep_positions_consistent(self):
        # Dois clientes com o quadro desatualizado soltam tarefas diferentes na mesma posição.
        self.move(self.a, self.doing, 0, self.owner)
        self.move(self.b, self.doing, 0, self.member)
        self.move(self.c, self.doing, 0, self.owner)

        self.assertEqual(self.board(self.doing), [('C', 0), ('B', 1), ('A', 2)])
        self.assertEqual(self.board(self.todo), [('D', 0)])

    def test_move_is_atomic(self):
        original_save = Task.save

        def fail_on_moved_task(task, *args, **kwargs):
            if task.pk == self.b.pk:
                raise RuntimeError('falhou no meio da movimentação')
            return original_save(task, *args, **kwargs)

        with mock.patch.object(Task, 'save', fail_on_moved_task), self.assertRaises(RuntimeError):
            self.move(self.b, self.doing, 0)

        self.assertEqual(self.board(self.todo), [('A', 0), ('B', 1), ('C', 2), ('D', 3)])
        self.assertEqual(self.board(self.doing), [])
