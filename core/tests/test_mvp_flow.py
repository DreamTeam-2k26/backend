"""Fluxo demonstrável do MVP, de ponta a ponta, só pela API e com JWT de verdade.

Cada passo já tem testes próprios na sua task; aqui o que importa é a sequência funcionar inteira e os dados
continuarem lá depois de um novo login (recarregar a página).
"""

from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from core.models import Membership

from .helpers import jwt_client


def register(email, name, password='Senha-forte-123'):
    response = APIClient().post('/api/register/', {'email': email, 'name': name, 'password': password})
    assert response.status_code == status.HTTP_201_CREATED, response.data  # noqa: S101
    return response.data['id']


class DemoFlowTests(APITestCase):
    PASSWORD = 'Senha-forte-123'

    def login(self, email):
        return jwt_client(email, self.PASSWORD)

    def test_register_invite_work_and_conclude(self):  # noqa: PLR0914 - o roteiro da demo, de propósito num teste só
        # 1) Cadastro e login (JWT real).
        register('ana@fluxo.dev', 'Ana')
        bia_id = register('bia@fluxo.dev', 'Bia')
        ana = self.login('ana@fluxo.dev')

        # 2) Projeto: a criadora vira dona e o quadro nasce com as 3 colunas.
        project = ana.post('/api/projects/', {'name': 'Feira de Ciências', 'description': 'Turma 3INFO'})
        self.assertEqual(project.status_code, 201)
        project_id = project.data['id']
        columns = ana.get('/api/columns/', {'project': project_id}).data['results']
        todo, doing, done = (column['id'] for column in columns)
        self.assertEqual([column['category'] for column in columns], ['todo', 'in_progress', 'done'])

        # 3) Convite direto: acha a Bia pelo e-mail e convida; antes de aceitar ela não vê nada.
        found = ana.get('/api/invites/find_user/', {'project': project_id, 'email': 'bia@fluxo.dev'})
        self.assertEqual(found.data, [{'id': bia_id, 'name': 'Bia'}])
        invite = ana.post('/api/invites/', {'project': project_id, 'kind': 'direct', 'invited_user': bia_id})
        self.assertEqual(invite.status_code, 201)
        bia = self.login('bia@fluxo.dev')
        self.assertEqual(bia.get(f'/api/projects/{project_id}/').status_code, 404)

        # 4) A Bia vê o convite pendente, aceita e entra como membro.
        received = bia.get('/api/invites/received/')
        self.assertEqual([item['id'] for item in received.data], [invite.data['id']])
        self.assertEqual(bia.post(f'/api/invites/{invite.data["id"]}/accept/').status_code, 200)
        self.assertEqual(bia.get(f'/api/projects/{project_id}/').data['role'], 'member')

        # 5) A Bia cria uma tarefa para si mesma, com prazo e prioridade.
        due = (timezone.localdate() + timedelta(days=3)).isoformat()
        task = bia.post(
            '/api/tasks/',
            {
                'project': project_id,
                'column': todo,
                'title': 'Montar o cartaz',
                'priority': 'high',
                'assignee': bia_id,
                'due_date': due,
            },
        )
        self.assertEqual(task.status_code, 201)
        task_id = task.data['id']

        # 6) A Ana move a tarefa para "Em andamento".
        moved = ana.post(f'/api/tasks/{task_id}/move/', {'column_id': doing, 'position': 0}, format='json')
        self.assertEqual(moved.status_code, 200)

        # 7) Checklist: dois itens, um concluído.
        first = bia.post('/api/checklist-items/', {'task': task_id, 'text': 'Comprar cartolina'})
        bia.post('/api/checklist-items/', {'task': task_id, 'text': 'Imprimir título'})
        self.assertEqual(bia.patch(f'/api/checklist-items/{first.data["id"]}/', {'is_done': True}).status_code, 200)

        # 8) Comentário com autor.
        comment = ana.post('/api/comments/', {'task': task_id, 'body': 'Ficou ótimo, pode concluir!'})
        self.assertEqual(comment.status_code, 201)

        dashboard = ana.get(f'/api/projects/{project_id}/dashboard/').data
        self.assertEqual((dashboard['in_progress_tasks'], dashboard['progress']), (1, 0))

        # 9) Concluir: mover para "Concluído".
        bia.post(f'/api/tasks/{task_id}/move/', {'column_id': done, 'position': 0}, format='json')

        # 10) "Recarregar a página": novo login e tudo continua lá.
        ana_again = self.login('ana@fluxo.dev')
        detail = ana_again.get(f'/api/tasks/{task_id}/').data
        self.assertEqual(
            (detail['column'], detail['assignee'], detail['priority'], detail['due_date']),
            (done, bia_id, 'high', due),
        )
        self.assertIsNotNone(detail['completed_at'])
        self.assertEqual((detail['checklist_total'], detail['checklist_done']), (2, 1))
        comments = ana_again.get('/api/comments/', {'task': task_id}).data['results']
        self.assertEqual([(c['author_name'], c['body']) for c in comments], [('Ana', 'Ficou ótimo, pode concluir!')])
        dashboard = ana_again.get(f'/api/projects/{project_id}/dashboard/').data
        self.assertEqual((dashboard['done_tasks'], dashboard['progress']), (1, 100))
        card = ana_again.get('/api/projects/').data['results'][0]
        self.assertEqual((card['progress'], card['total_memberships']), (100, 2))

    def test_whole_team_joins_by_one_reusable_link(self):
        register('ana@fluxo.dev', 'Ana')
        for name in ('bia', 'caio', 'duda'):
            register(f'{name}@fluxo.dev', name.title())
        ana = self.login('ana@fluxo.dev')
        project_id = ana.post('/api/projects/', {'name': 'Gincana'}).data['id']

        link = ana.post('/api/invites/', {'project': project_id, 'kind': 'link', 'role': 'admin'})
        token = link.data['token']

        # O mesmo link serve para várias pessoas; o papel vem do convite e o link continua pendente.
        for name in ('bia', 'caio'):
            joined = self.login(f'{name}@fluxo.dev').post('/api/invites/redeem/', {'token': token})
            self.assertEqual((joined.status_code, joined.data['role']), (200, 'admin'))
        self.assertEqual(ana.get(f'/api/invites/{link.data["id"]}/').data['status'], 'pending')

        # Revogado, o link para de servir para quem ainda não entrou, sem tirar quem já entrou.
        self.assertEqual(ana.post(f'/api/invites/{link.data["id"]}/revoke/').status_code, 200)
        late = self.login('duda@fluxo.dev').post('/api/invites/redeem/', {'token': token})
        self.assertEqual(late.status_code, 400)
        roles = dict(Membership.objects.filter(project_id=project_id).values_list('user__email', 'role'))
        self.assertEqual(roles, {'ana@fluxo.dev': 'owner', 'bia@fluxo.dev': 'admin', 'caio@fluxo.dev': 'admin'})
        self.assertEqual(self.login('bia@fluxo.dev').get(f'/api/projects/{project_id}/').status_code, 200)
