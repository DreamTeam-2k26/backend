from django.core.management.base import BaseCommand
from django.db import transaction

from chat import services
from chat.models import Message, Room


class Command(BaseCommand):
    help = (
        'Apaga as salas avulsas antigas (sem projeto), com suas mensagens e arquivos. '
        'Os usuários não são tocados. Sem --yes só mostra o que seria apagado.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--yes', action='store_true', help='Confirma e apaga de verdade.')

    def handle(self, *args, **options):
        rooms = Room.objects.filter(project__isnull=True)
        total = rooms.count()

        for room in rooms:
            messages = Message.objects.filter(room=room).count()
            members = room.memberships.count()
            self.stdout.write(f'- sala #{room.pk} "{room.name}": {members} participantes, {messages} mensagens')

        if not total:
            self.stdout.write('Nenhuma sala avulsa. Nada a fazer.')
            return

        if not options['yes']:
            self.stdout.write(self.style.WARNING(f'{total} sala(s) seriam apagadas. Rode com --yes para confirmar.'))
            return

        with transaction.atomic():
            for room in rooms:
                services.delete_room_files(room)

            deleted, _ = rooms.delete()

        self.stdout.write(self.style.SUCCESS(f'{total} sala(s) avulsa(s) apagada(s) ({deleted} registros no total).'))
