from django.core.management.base import BaseCommand

from chat.models import UserPresence


class Command(BaseCommand):
    help = 'Zera os contadores de conexões do chat (use após reiniciar o servidor: conexões abertas somem sem avisar).'

    def handle(self, *args, **options):
        updated = UserPresence.objects.filter(connections__gt=0).update(connections=0)
        self.stdout.write(self.style.SUCCESS(f'{updated} usuário(s) marcados como offline.'))
