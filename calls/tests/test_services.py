from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from calls import services
from calls.models import Call, Participant

from .helpers import future, live_call, scheduled_call, setup_team


class StartAndScheduleTests(TestCase):
    def setUp(self):
        self.project, self.owner, self.admin, self.member, self.outsider = setup_team()

    def test_dono_e_admin_iniciam_membro_e_fora_nao(self):
        self.assertEqual(live_call(self.project, self.owner).status, Call.Status.LIVE)
        Call.objects.all().delete()
        self.assertEqual(live_call(self.project, self.admin).status, Call.Status.LIVE)
        Call.objects.all().delete()

        with self.assertRaisesMessage(PermissionDenied, 'dono ou um administrador'):
            services.start_now(self.project, self.member)

        with self.assertRaisesMessage(PermissionDenied, 'não encontrado'):
            services.start_now(self.project, self.outsider)

    def test_so_uma_chamada_em_andamento_por_projeto(self):
        live_call(self.project, self.owner)

        with self.assertRaisesMessage(ValidationError, 'Já existe uma chamada em andamento'):
            live_call(self.project, self.admin)

    def test_titulo_padrao_e_limite(self):
        self.assertEqual(services.start_now(self.project, self.owner, '   ').title, services.DEFAULT_TITLE)
        Call.objects.all().delete()

        with self.assertRaises(ValidationError):
            services.start_now(self.project, self.owner, 'x' * 151)

    def test_agendar_exige_futuro_e_papel(self):
        call = services.schedule(self.project, self.admin, ' Sprint ', future(), '  pauta  ')
        self.assertEqual((call.title, call.description, call.status), ('Sprint', 'pauta', Call.Status.SCHEDULED))

        with self.assertRaises(ValidationError):
            services.schedule(self.project, self.owner, 'Passado', timezone.now() - timedelta(hours=1))

        with self.assertRaises(ValidationError):
            services.schedule(self.project, self.owner, 'Sem data', None)

        with self.assertRaises(PermissionDenied):
            services.schedule(self.project, self.member, 'Não pode', future())

    def test_editar_so_agendada(self):
        call = scheduled_call(self.project, self.owner)
        services.update_scheduled(call, self.admin, title='Novo', scheduled_for=future(5))
        call.refresh_from_db()
        self.assertEqual(call.title, 'Novo')
        services.start_scheduled(call, self.owner)

        with self.assertRaisesMessage(ValidationError, 'agendadas'):
            services.update_scheduled(call, self.owner, title='Tarde demais')

    def test_iniciar_agendada_respeita_uma_por_projeto(self):
        call = scheduled_call(self.project, self.owner)
        live_call(self.project, self.owner)

        with self.assertRaises(ValidationError):
            services.start_scheduled(call, self.owner)

    def test_encerrar_e_cancelar(self):
        call = live_call(self.project, self.owner)

        with self.assertRaises(PermissionDenied):
            services.end_call(call, self.member)

        with self.assertRaises(ValidationError):
            services.cancel(call, self.owner)

        services.end_call(call, self.admin)
        self.assertEqual(call.status, Call.Status.ENDED)
        services.cancel(call, self.owner)
        self.assertFalse(Call.objects.filter(pk=call.pk).exists())


class JoinLeaveTests(TestCase):
    def setUp(self):
        self.project, self.owner, self.admin, self.member, self.outsider = setup_team()
        self.call = live_call(self.project, self.owner)

    def test_membro_entra_e_fora_nao(self):
        participant, replaced = services.join(self.call.pk, self.member, 'canal-1')
        self.assertIsNone(replaced)
        self.assertIsNone(participant.left_at)

        with self.assertRaises(PermissionDenied):
            services.join(self.call.pk, self.outsider, 'canal-x')

    def test_nao_entra_em_agendada_nem_encerrada(self):
        scheduled = scheduled_call(self.project, self.owner)

        with self.assertRaisesMessage(ValidationError, 'não está em andamento'):
            services.join(scheduled.pk, self.owner, 'c')

        services.end_call(self.call)

        with self.assertRaises(ValidationError):
            services.join(self.call.pk, self.owner, 'c')

    def test_outra_aba_assume_o_lugar(self):
        services.join(self.call.pk, self.member, 'aba-1')
        _, replaced = services.join(self.call.pk, self.member, 'aba-2')
        self.assertEqual(replaced, 'aba-1')
        self.assertEqual(Participant.objects.filter(call=self.call).count(), 1)
        # a aba antiga saindo não derruba a nova
        self.assertEqual(services.leave(self.call.pk, self.member, 'aba-1'), (False, False))
        self.assertEqual(services.leave(self.call.pk, self.member, 'aba-2')[0], True)

    def test_limite_de_participantes(self):
        with self.settings(CALL_MAX_PARTICIPANTS=2):
            services.join(self.call.pk, self.owner, 'a')
            services.join(self.call.pk, self.admin, 'b')

            with self.assertRaisesMessage(ValidationError, 'cheia'):
                services.join(self.call.pk, self.member, 'c')

            # quem já está dentro pode reentrar mesmo com a sala cheia
            services.join(self.call.pk, self.owner, 'a2')

    def test_ultima_pessoa_saindo_encerra(self):
        services.join(self.call.pk, self.owner, 'a')
        services.join(self.call.pk, self.admin, 'b')
        self.assertEqual(services.leave(self.call.pk, self.owner, 'a'), (True, False))
        self.assertEqual(services.leave(self.call.pk, self.admin, 'b'), (True, True))
        self.call.refresh_from_db()
        self.assertEqual(self.call.status, Call.Status.ENDED)

    def test_reentrar_depois_de_sair(self):
        services.join(self.call.pk, self.owner, 'a')
        services.join(self.call.pk, self.admin, 'b')
        services.leave(self.call.pk, self.admin, 'b')
        participant, replaced = services.join(self.call.pk, self.admin, 'b2')
        self.assertIsNone(replaced)
        self.assertIsNone(participant.left_at)

    def test_estado_so_campos_conhecidos(self):
        state = services.sanitize_state({'mic': 1, 'camera': 0, 'screen': True, 'screen_stream': 'abc', 'x': 'y'})
        self.assertEqual(state, {'mic': True, 'camera': False, 'screen': True, 'hand': False, 'screen_stream': 'abc'})
        self.assertEqual(services.sanitize_state('lixo')['screen_stream'], '')
        self.assertEqual(services.sanitize_state({'screen_stream': 'abc'})['screen_stream'], '')

    def test_chamada_vazia_antiga_e_encerrada(self):
        Call.objects.filter(pk=self.call.pk).update(started_at=timezone.now() - timedelta(minutes=30))
        self.assertEqual([c.pk for c in services.end_stale_calls()], [self.call.pk])
        self.call.refresh_from_db()
        self.assertEqual(self.call.status, Call.Status.ENDED)

    def test_chamada_com_gente_nao_e_encerrada(self):
        services.join(self.call.pk, self.owner, 'a')
        Call.objects.filter(pk=self.call.pk).update(started_at=timezone.now() - timedelta(minutes=30))
        self.assertEqual(services.end_stale_calls(), [])
