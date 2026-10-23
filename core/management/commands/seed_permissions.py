from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand, CommandError

from core.models import Group

# Permissões mínimas de um usuário recém-registrado (grupo padrão).
BASELINE_PERMISSIONS = ('uploader.add_image',)


class Command(BaseCommand):
    help = 'Aplica o conjunto mínimo de permissões ao grupo padrão (ou ao grupo indicado).'

    def add_arguments(self, parser):
        parser.add_argument('--group', help='Slug do grupo alvo. Sem ele, usa o grupo is_default.')
        parser.add_argument('--dry-run', action='store_true', help='Mostra o que seria alterado, sem gravar.')

    def handle(self, *args, **options):
        group = self._get_group(options['group'])
        permissions = self._get_permissions()
        missing = [permission for permission in permissions if not group.permissions.filter(pk=permission.pk).exists()]

        if options['dry_run']:
            self.stdout.write(f'Grupo "{group.name}" receberia: {self._labels(missing) or "nada"}')

            return

        group.permissions.add(*missing)
        self.stdout.write(self.style.SUCCESS(f'Grupo "{group.name}": {self._labels(missing) or "nada a fazer"}.'))

    @staticmethod
    def _get_group(slug):
        if slug:
            try:
                return Group.objects.get(slug=slug)
            except Group.DoesNotExist as error:
                raise CommandError(f'Grupo "{slug}" não encontrado.') from error

        group = Group.objects.filter(is_default=True).first()

        if not group:
            raise CommandError('Nenhum grupo padrão definido. Crie um grupo com is_default=True ou use --group.')

        return group

    @staticmethod
    def _get_permissions():
        permissions = []

        for label in BASELINE_PERMISSIONS:
            app_label, codename = label.split('.', 1)

            try:
                permissions.append(Permission.objects.get(content_type__app_label=app_label, codename=codename))
            except Permission.DoesNotExist as error:
                raise CommandError(f'Permissão "{label}" não encontrada.') from error

        return permissions

    @staticmethod
    def _labels(permissions):
        return ', '.join(f'{permission.content_type.app_label}.{permission.codename}' for permission in permissions)
