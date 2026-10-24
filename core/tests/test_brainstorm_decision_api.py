import datetime
import json
from unittest import mock

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from core import ai
from core.models import (
    BrainstormAnalysis,
    BrainstormDecision,
    BrainstormDecisionTask,
    BrainstormTopic,
    Column,
    Task,
)

from .helpers import client_for, make_project, make_user
from .test_brainstorm_topic_api import topic_url

RESULT = {
    'main_solution': 'Robótica sustentável',
    'reasoning': 'Mais votada.',
    'alternatives': [],
    'sources': [],
    'ai_additions': [],
    'task_suggestions': [
        {
            'title': 'Montar protótipo',
            'description': 'Com sucata',
            'priority': 'high',
            'existing_task_id': None,
            'sources': [],
        },
        {'title': 'Fazer cartaz', 'description': '', 'priority': 'low', 'existing_task_id': None, 'sources': []},
    ],
}


class DecisionTests(APITestCase):
    def setUp(self):
        self.owner = make_user('dona')
        self.admin = make_user('admin')
        self.member = make_user('membro')
        self.outsider = make_user('externo')
        self.project = make_project(self.owner, admins=[self.admin], members=[self.member])
        self.other_project = make_project(self.outsider, name='Outro projeto')
        self.todo = self.project.columns.get(category=Column.Category.TODO)
        self.existing = Task.objects.create(
            project=self.project, column=self.project.columns.get(category='in_progress'), title='Comprar materiais'
        )
        self.foreign_task = Task.objects.create(
            project=self.other_project, column=self.other_project.columns.first(), title='Alheia'
        )
        self.topic = BrainstormTopic.objects.create(
            project=self.project,
            author=self.member,
            title='Tema',
            context='Escolher.',
            status=BrainstormTopic.Status.CLOSED,
        )
        self.analysis = self.ready_analysis()
        self.client = client_for(self.admin)

    def ready_analysis(self, cycle=None):
        return BrainstormAnalysis.objects.create(
            topic=self.topic, cycle=cycle or self.topic.cycle, status='ready', result=RESULT
        )

    def payload(self, **extra):
        data = {'analysis': self.analysis.pk, 'text': 'Faremos robótica sustentável', 'justification': 'Votação'}
        data.update(extra)
        return data

    def approve(self, user=None, **extra):
        client = client_for(user) if user else self.client
        return client.post(topic_url(self.topic, 'approve'), self.payload(**extra), format='json')

    def test_full_approval_creates_and_links_tasks(self):
        due = (timezone.localdate() + datetime.timedelta(days=7)).isoformat()
        tasks = [
            {
                'suggestion_index': 0,
                'title': 'Montar protótipo v1',
                'description': 'Editado',
                'priority': 'high',
                'assignee': self.member.pk,
                'due_date': due,
            },
            {'existing_task_id': self.existing.pk},
        ]
        response = self.approve(tasks=tasks)
        self.assertEqual(response.status_code, 201, response.data)
        created = Task.objects.get(title='Montar protótipo v1')
        self.assertEqual((created.column, created.assignee, created.created_by), (self.todo, self.member, self.admin))
        self.existing.refresh_from_db()
        self.assertEqual(self.existing.title, 'Comprar materiais')
        self.assertEqual(
            [(item['task'], item['created'], item['suggestion_index']) for item in response.data['tasks']],
            [(created.pk, True, 0), (self.existing.pk, False, None)],
        )
        self.assertEqual(response.data['rejected_suggestions'], [1])
        self.topic.refresh_from_db()
        self.assertEqual(self.topic.status, 'decided')
        self.assertEqual(self.topic.current_decision.pk, response.data['id'])
        self.assertEqual(response.data['approved_by'], self.admin.pk)

    def test_no_selection_creates_no_task(self):
        before = Task.objects.count()
        self.assertEqual(self.approve().status_code, 201)
        self.assertEqual(Task.objects.count(), before)
        self.assertEqual(BrainstormDecision.objects.get().rejected_suggestions, [0, 1])

    def test_repeated_post_is_idempotent(self):
        tasks = [{'suggestion_index': 0, 'title': 'Montar protótipo'}]
        first = self.approve(tasks=tasks)
        second = self.approve(tasks=tasks)
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertEqual(second.data['id'], first.data['id'])
        self.assertEqual(Task.objects.filter(title='Montar protótipo').count(), 1)
        self.assertEqual(BrainstormDecision.objects.count(), 1)

    def test_other_analysis_in_same_cycle_conflicts(self):
        self.approve()
        response = self.approve(analysis=self.ready_analysis().pk)
        self.assertEqual(response.status_code, 409)

    def test_requires_ready_analysis_of_current_cycle(self):
        failed = BrainstormAnalysis.objects.create(topic=self.topic, cycle=1, status='failed')
        self.assertEqual(self.approve(analysis=failed.pk).status_code, 400)
        self.topic.cycle = 2
        self.topic.save()
        self.assertEqual(self.approve().status_code, 400)
        other_topic = BrainstormTopic.objects.create(project=self.project, title='x', context='y', status='closed')
        foreign = BrainstormAnalysis.objects.create(topic=other_topic, cycle=1, status='ready', result=RESULT)
        self.assertEqual(self.approve(analysis=foreign.pk).status_code, 400)

    def test_open_topic_cannot_be_approved(self):
        self.topic.status = 'open'
        self.topic.save()
        self.assertEqual(self.approve().status_code, 400)

    def test_invalid_task_rolls_back_everything(self):
        cases = [
            [{'title': 'A', 'assignee': self.outsider.pk}],
            [{'title': 'A', 'due_date': '2020-01-01'}],
            [{'existing_task_id': self.foreign_task.pk}],
            [{'existing_task_id': self.existing.pk}, {'existing_task_id': self.existing.pk}],
            [{'suggestion_index': 9, 'title': 'A'}],
            [{'description': 'sem título'}],
            [{'title': 'A', 'priority': 'urgente'}],
        ]
        before = Task.objects.count()
        for tasks in cases:
            with self.subTest(tasks=tasks):
                response = self.approve(tasks=[{'title': 'Válida'}, *tasks])
                self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(Task.objects.count(), before)
        self.assertFalse(BrainstormDecision.objects.exists())
        self.topic.refresh_from_db()
        self.assertEqual(self.topic.status, 'closed')

    def test_project_without_todo_column(self):
        self.todo.category = Column.Category.IN_PROGRESS
        self.todo.save()
        self.assertEqual(self.approve(tasks=[{'title': 'A'}]).status_code, 400)
        self.assertEqual(self.approve().status_code, 201)

    def test_permissions(self):
        self.assertEqual(self.approve(self.member).status_code, 403)
        self.assertEqual(self.approve(self.outsider).status_code, 404)
        self.assertEqual(self.approve(self.owner).status_code, 201)

    def test_text_required(self):
        self.assertEqual(self.approve(text='').status_code, 400)

    def test_reopen_keeps_current_decision_in_review_and_new_approval_replaces_it(self):
        first = self.approve(tasks=[{'title': 'Primeira'}]).data
        self.client.post(topic_url(self.topic, 'reopen'))
        detail = self.client.get(topic_url(self.topic)).data
        self.assertEqual((detail['status'], detail['cycle'], detail['in_review']), ('open', 2, True))
        self.assertEqual(detail['current_decision']['id'], first['id'])

        self.client.post(topic_url(self.topic, 'close'))
        old = self.approve()
        self.assertEqual(old.status_code, 400)
        self.topic.refresh_from_db()
        self.analysis = self.ready_analysis()
        second = self.approve(text='Versão 2')
        self.assertEqual((second.status_code, second.data['cycle']), (201, 2))
        detail = self.client.get(topic_url(self.topic)).data
        self.assertEqual((detail['in_review'], detail['current_decision']['id']), (False, second.data['id']))
        self.assertTrue(Task.objects.filter(title='Primeira').exists())
        self.assertEqual(BrainstormDecisionTask.objects.filter(decision_id=first['id']).count(), 1)

    def test_versions_lists_each_cycle(self):
        self.approve()
        self.client.post(topic_url(self.topic, 'reopen'))
        response = client_for(self.member).get(topic_url(self.topic, 'versions'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['cycle'] for item in response.data], [1, 2])
        self.assertEqual(response.data[0]['decision']['text'], 'Faremos robótica sustentável')
        self.assertTrue(response.data[0]['current'])
        self.assertEqual(response.data[0]['analyses'][0]['id'], self.analysis.pk)
        self.assertIsNone(response.data[1]['decision'])


class FullFlowTests(APITestCase):
    def test_topic_to_kanban_through_the_api(self):
        owner, member = make_user('dona'), make_user('membro')
        project = make_project(owner, members=[member])
        as_owner, as_member = client_for(owner), client_for(member)

        topic = as_member.post(
            '/api/brainstorm-topics/', {'project': project.pk, 'title': 'Tema', 'context': 'Escolher o tema.'}
        ).data
        url = f'/api/brainstorm-topics/{topic["id"]}/'
        reply = as_member.post('/api/brainstorm-replies/', {'topic': topic['id'], 'content': 'Robótica'}).data
        as_owner.post('/api/brainstorm-replies/', {'topic': topic['id'], 'parent': reply['id'], 'content': 'Sim!'})
        as_owner.post(f'/api/brainstorm-replies/{reply["id"]}/vote/', {'value': 1}, format='json')
        as_owner.post(f'{url}close/')

        answer = {
            'main_solution': 'Robótica',
            'reasoning': 'Votada',
            'alternatives': [],
            'sources': [reply['id']],
            'ai_additions': [],
            'task_suggestions': [{'title': 'Montar robô', 'priority': 'high'}],
        }
        with (
            override_settings(AI_API_KEY='chave'),
            mock.patch.object(ai, 'complete', return_value=json.dumps(answer)),
        ):
            analysis = as_owner.post(f'{url}analyze/').data
        self.assertEqual(analysis['result']['sources'], [reply['id']])

        decision = as_owner.post(
            f'{url}approve/',
            {
                'analysis': analysis['id'],
                'text': 'Robótica',
                'tasks': [{'suggestion_index': 0, 'title': 'Montar robô', 'priority': 'high'}],
            },
            format='json',
        )
        self.assertEqual(decision.status_code, 201, decision.data)
        board = as_member.get('/api/tasks/', {'project': project.pk}).data['results']
        self.assertEqual([task['title'] for task in board], ['Montar robô'])
        self.assertEqual(as_member.get(url).data['status'], 'decided')
