from rest_framework.test import APIClient, APITestCase

from core.models import ChecklistItem, Task

from .helpers import client_for, make_project, make_user

ITEMS = '/api/checklist-items/'
REORDER = f'{ITEMS}reorder/'


def item_url(item):
    return f'{ITEMS}{item.pk}/'


class ChecklistAPITestCase(APITestCase):
    def setUp(self):
        self.owner = make_user('dono')
        self.member = make_user('membro')
        self.outsider = make_user('externo')
        self.project = make_project(self.owner, members=[self.member])
        self.other_project = make_project(self.outsider, name='Outro projeto')
        self.todo, self.doing, self.done = self.project.columns.order_by('position')
        self.task = self.make_task('Cartaz')
        self.other_task = Task.objects.create(
            project=self.other_project, column=self.other_project.columns.first(), title='Alheia'
        )
        self.a, self.b, self.c = (self.make_item(text, pos) for pos, text in enumerate(['A', 'B', 'C']))

    def make_task(self, title, column=None):
        return Task.objects.create(project=self.project, column=column or self.todo, title=title)

    def make_item(self, text, position=0, task=None, **extra):
        return ChecklistItem.objects.create(task=task or self.task, text=text, position=position, **extra)

    def texts(self, response):
        return [item['text'] for item in response.data['results']]


class AuthenticationTests(ChecklistAPITestCase):
    def test_endpoints_require_authentication(self):
        anonymous = APIClient()
        requests = [
            ('get', ITEMS),
            ('post', ITEMS),
            ('post', REORDER),
            ('get', item_url(self.a)),
            ('patch', item_url(self.a)),
            ('delete', item_url(self.a)),
        ]
        for method, url in requests:
            with self.subTest(method=method, url=url):
                self.assertEqual(getattr(anonymous, method)(url).status_code, 401)


class ListAndDetailTests(ChecklistAPITestCase):
    def test_member_lists_items_of_task_in_order(self):
        self.a.position = 9
        self.a.save()

        response = client_for(self.member).get(ITEMS, {'task': self.task.pk})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.texts(response), ['B', 'C', 'A'])

    def test_items_of_other_projects_are_isolated(self):
        self.make_item('Alheio', task=self.other_task)

        self.assertEqual(self.texts(client_for(self.member).get(ITEMS)), ['A', 'B', 'C'])
        self.assertEqual(client_for(self.member).get(ITEMS, {'task': self.other_task.pk}).data['results'], [])

    def test_member_sees_detail(self):
        response = client_for(self.member).get(item_url(self.b))

        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.data['text'], response.data['is_done']), ('B', False))

    def test_outsider_gets_404(self):
        self.assertEqual(client_for(self.outsider).get(item_url(self.a)).status_code, 404)


class CreateItemTests(ChecklistAPITestCase):
    def post(self, user=None, **payload):
        return client_for(user or self.member).post(ITEMS, {'task': self.task.pk, 'text': 'Imprimir', **payload})

    def test_member_creates_item_at_the_end(self):
        response = self.post()

        self.assertEqual(response.status_code, 201)
        item = ChecklistItem.objects.get(pk=response.data['id'])
        self.assertEqual((item.task, item.text, item.is_done, item.position), (self.task, 'Imprimir', False, 3))

    def test_first_item_of_task_starts_at_zero(self):
        empty_task = self.make_task('Vazia')

        response = client_for(self.member).post(ITEMS, {'task': empty_task.pk, 'text': 'Primeiro'})

        self.assertEqual(response.data['position'], 0)

    def test_text_is_required(self):
        response = self.post(text='   ')

        self.assertEqual(response.status_code, 400)
        self.assertIn('text', response.data)

    def test_outsider_gets_404(self):
        self.assertEqual(self.post(self.outsider).status_code, 404)

    def test_task_of_another_project_gets_404(self):
        response = client_for(self.member).post(ITEMS, {'task': self.other_task.pk, 'text': 'Invasão'})

        self.assertEqual(response.status_code, 404)
        self.assertFalse(ChecklistItem.objects.filter(task=self.other_task).exists())


class UpdateItemTests(ChecklistAPITestCase):
    def patch(self, item, user=None, **payload):
        return client_for(user or self.member).patch(item_url(item), payload, format='json')

    def test_member_marks_item_done_and_undone(self):
        self.assertEqual(self.patch(self.b, is_done=True).status_code, 200)
        self.b.refresh_from_db()
        self.assertTrue(self.b.is_done)

        self.patch(self.b, is_done=False)
        self.b.refresh_from_db()
        self.assertFalse(self.b.is_done)

    def test_member_renames_item(self):
        self.patch(self.a, text='A revisado')

        self.a.refresh_from_db()
        self.assertEqual(self.a.text, 'A revisado')

    def test_item_cannot_move_to_another_task(self):
        other = self.make_task('Outra tarefa')

        response = self.patch(self.a, task=other.pk)

        self.assertEqual(response.status_code, 400)
        self.assertIn('task', response.data)
        self.a.refresh_from_db()
        self.assertEqual(self.a.task, self.task)

    def test_outsider_gets_404(self):
        self.assertEqual(self.patch(self.a, self.outsider, is_done=True).status_code, 404)


class DeleteItemTests(ChecklistAPITestCase):
    def test_member_deletes_item(self):
        response = client_for(self.member).delete(item_url(self.b))

        self.assertEqual(response.status_code, 204)
        self.assertEqual(list(self.task.checklist_items.values_list('text', flat=True)), ['A', 'C'])

    def test_outsider_gets_404(self):
        self.assertEqual(client_for(self.outsider).delete(item_url(self.b)).status_code, 404)
        self.assertTrue(ChecklistItem.objects.filter(pk=self.b.pk).exists())


class ReorderItemsTests(ChecklistAPITestCase):
    def reorder(self, ids, user=None, task=None):
        payload = {'task': (task or self.task).pk, 'items': ids}
        return client_for(user or self.member).post(REORDER, payload, format='json')

    def test_member_reorders_items(self):
        response = self.reorder([self.c.pk, self.a.pk, self.b.pk])

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['text'] for item in response.data], ['C', 'A', 'B'])
        listed = client_for(self.member).get(ITEMS, {'task': self.task.pk})
        self.assertEqual(self.texts(listed), ['C', 'A', 'B'])
        self.assertEqual(sorted(item['position'] for item in listed.data['results']), [0, 1, 2])

    def test_must_list_every_item_of_task_once(self):
        foreign = self.make_item('Alheio', task=self.other_task)
        cases = {
            'faltando': [self.c.pk, self.a.pk],
            'repetido': [self.c.pk, self.a.pk, self.b.pk, self.a.pk],
            'de outra tarefa': [self.c.pk, self.a.pk, foreign.pk],
        }
        for label, ids in cases.items():
            with self.subTest(label):
                response = self.reorder(ids)
                self.assertEqual(response.status_code, 400)
                self.assertIn('items', response.data)
        self.assertEqual(list(self.task.checklist_items.values_list('text', flat=True)), ['A', 'B', 'C'])

    def test_outsider_gets_404(self):
        foreign = self.make_item('Alheio', task=self.other_task)

        self.assertEqual(self.reorder([foreign.pk], self.member, task=self.other_task).status_code, 404)


class TaskChecklistCountsTests(ChecklistAPITestCase):
    def setUp(self):
        super().setUp()
        self.a.is_done = True
        self.a.save()

    def test_task_list_and_detail_show_checklist_counts(self):
        listed = client_for(self.member).get('/api/tasks/', {'project': self.project.pk}).data['results'][0]
        detail = client_for(self.member).get(f'/api/tasks/{self.task.pk}/').data

        for data in (listed, detail):
            with self.subTest(data=data['title']):
                self.assertEqual((data['checklist_total'], data['checklist_done']), (3, 1))

    def test_task_without_items_shows_zero(self):
        self.make_task('Sem itens')

        listed = client_for(self.member).get('/api/tasks/', {'project': self.project.pk}).data['results']

        empty = next(task for task in listed if task['title'] == 'Sem itens')
        self.assertEqual((empty['checklist_total'], empty['checklist_done']), (0, 0))

    def test_move_response_keeps_checklist_counts(self):
        response = client_for(self.member).post(
            f'/api/tasks/{self.task.pk}/move/', {'column_id': self.doing.pk, 'position': 0}, format='json'
        )

        moved = next(task for task in response.data if task['id'] == self.task.pk)
        self.assertEqual((moved['checklist_total'], moved['checklist_done']), (3, 1))
