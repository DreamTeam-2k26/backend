from django.test import TestCase
from rest_framework.test import APIClient, APITestCase

from calls import services
from calls.models import Call
from core.models import Membership

from .helpers import client_for, future, live_call, make_project, scheduled_call, setup_team

URL = '/api/calls/'


class CallApiTests(APITestCase):
    def setUp(self):
        self.project, self.owner, self.admin, self.member, self.outsider = setup_team()

    def post(self, user, **body):
        return client_for(user).post(URL, {'project': self.project.pk, **body}, format='json')

    def test_dono_inicia_chamada_na_hora(self):
        response = self.post(self.owner, title='Daily')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['status'], 'live')
        self.assertEqual(response.data['title'], 'Daily')
        self.assertTrue(response.data['can_manage'])
        self.assertEqual(response.data['participants_count'], 0)

    def test_membro_nao_inicia_nem_agenda(self):
        self.assertEqual(self.post(self.member).status_code, 403)
        self.assertEqual(self.post(self.member, scheduled_for=future().isoformat()).status_code, 403)

    def test_fora_do_projeto_recebe_404(self):
        self.assertEqual(self.post(self.outsider).status_code, 404)

    def test_agendar_futuro_e_rejeitar_passado(self):
        ok = self.post(self.admin, title='Sprint', scheduled_for=future().isoformat(), description='pauta')
        self.assertEqual(ok.status_code, 201, ok.data)
        self.assertEqual(ok.data['status'], 'scheduled')
        self.assertEqual(self.post(self.admin, scheduled_for='2020-01-01T10:00:00Z').status_code, 400)

    def test_segunda_chamada_em_andamento_da_400(self):
        self.post(self.owner)
        response = self.post(self.admin)
        self.assertEqual(response.status_code, 400)
        self.assertIn('andamento', str(response.data))

    def test_listar_filtrar_e_isolar_projetos(self):
        scheduled_call(self.project, self.owner, 'Minha')
        services.start_now(make_other_project(self.outsider), self.outsider, 'Alheia')
        titles = [item['title'] for item in _results(client_for(self.member).get(URL))]
        self.assertEqual(titles, ['Minha'])
        filtered = client_for(self.owner).get(URL, {'project': self.project.pk, 'status': 'live'})
        self.assertEqual(_results(filtered), [])

    def test_live_lista_so_em_andamento(self):
        scheduled_call(self.project, self.owner)
        live_call(self.project, self.owner, 'Agora')
        data = client_for(self.member).get(URL + 'live/').data
        self.assertEqual([c['title'] for c in data], ['Agora'])
        self.assertFalse(data[0]['can_manage'])
        self.assertEqual(client_for(self.outsider).get(URL + 'live/').data, [])

    def test_iniciar_agendada_e_encerrar(self):
        call = scheduled_call(self.project, self.owner)
        self.assertEqual(client_for(self.member).post(f'{URL}{call.pk}/start/').status_code, 403)
        started = client_for(self.admin).post(f'{URL}{call.pk}/start/')
        self.assertEqual(started.data['status'], 'live')
        self.assertEqual(client_for(self.member).post(f'{URL}{call.pk}/end/').status_code, 403)
        ended = client_for(self.owner).post(f'{URL}{call.pk}/end/')
        self.assertEqual(ended.data['status'], 'ended')
        self.assertEqual(client_for(self.owner).post(f'{URL}{call.pk}/end/').status_code, 400)

    def test_editar_agendada(self):
        call = scheduled_call(self.project, self.owner)
        response = client_for(self.admin).patch(f'{URL}{call.pk}/', {'title': 'Outro nome'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['title'], 'Outro nome')
        self.assertEqual(
            client_for(self.member).patch(f'{URL}{call.pk}/', {'title': 'x'}, format='json').status_code, 403
        )

    def test_cancelar_e_nao_apagar_em_andamento(self):
        call = scheduled_call(self.project, self.owner)
        self.assertEqual(client_for(self.member).delete(f'{URL}{call.pk}/').status_code, 403)
        self.assertEqual(client_for(self.owner).delete(f'{URL}{call.pk}/').status_code, 204)
        live = live_call(self.project, self.owner)
        self.assertEqual(client_for(self.owner).delete(f'{URL}{live.pk}/').status_code, 400)

    def test_chamada_de_outro_projeto_da_404(self):
        call = scheduled_call(self.project, self.owner)
        self.assertEqual(client_for(self.outsider).get(f'{URL}{call.pk}/').status_code, 404)
        self.assertEqual(client_for(self.outsider).post(f'{URL}{call.pk}/start/').status_code, 404)

    def test_exige_login_e_config(self):
        self.assertEqual(APIClient().get(URL).status_code, 401)
        config = client_for(self.member).get(URL + 'config/').data
        self.assertTrue(config['ice_servers'])
        self.assertGreaterEqual(config['max_participants'], 2)

    def test_papel_muda_o_que_pode(self):
        Membership.objects.filter(project=self.project, user=self.member).update(role=Membership.Role.ADMIN)
        self.assertEqual(self.post(self.member).status_code, 201)


def make_other_project(owner):
    return make_project(owner, 'Outro')


def _results(response):
    data = response.data

    return data['results'] if isinstance(data, dict) else data


class CallModelTests(TestCase):
    def test_str(self):
        project, owner, *_ = setup_team()
        self.assertEqual(str(live_call(project, owner, 'Oi')), 'Oi')
        self.assertTrue(Call.objects.exists())
