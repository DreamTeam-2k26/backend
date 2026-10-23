from django.test import TestCase

from core.models import Column, Project

from .helpers import make_user


class DefaultColumnsTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Gincana', created_by=make_user('dono'))

    def test_creates_three_initial_columns_in_order(self):
        Column.create_defaults(self.project)

        columns = list(self.project.columns.order_by('position').values_list('name', 'category', 'position'))
        self.assertEqual(
            columns,
            [('A fazer', 'todo', 0), ('Em andamento', 'in_progress', 1), ('Concluído', 'done', 2)],
        )

    def test_repeated_calls_do_not_duplicate(self):
        Column.create_defaults(self.project)
        Column.create_defaults(self.project)

        self.assertEqual(self.project.columns.count(), 3)

    def test_categories(self):
        self.assertEqual(Column.Category.values, ['todo', 'in_progress', 'done'])

    def test_str(self):
        Column.create_defaults(self.project)

        self.assertEqual(str(self.project.columns.get(position=0)), 'A fazer (Gincana)')
