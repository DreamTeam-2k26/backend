from django.contrib.auth.management import create_permissions
from django.db import migrations

# Mesmo conjunto de core/management/commands/seed_permissions.py (BASELINE_PERMISSIONS). Copiado aqui de
# propósito: migrations não devem importar código da aplicação, que pode mudar depois.
BASELINE_PERMISSIONS = (('uploader', 'add_image'),)
DEFAULT_GROUP_NAME = 'Usuários'
DEFAULT_GROUP_SLUG = 'usuarios'


def ensure_default_group(apps, schema_editor):
    """Garante o grupo padrão com as permissões mínimas de quem se cadastra (ex.: enviar foto de perfil).

    O signal core.signals.assign_default_group já põe todo usuário novo nesse grupo; faltava o grupo existir
    e ter as permissões sem passo manual. Usuários antigos sem nenhum grupo também entram nele.
    """
    # Num banco novo as permissões só são criadas no post_migrate, depois de todas as migrations.
    for app_label in {label for label, _ in BASELINE_PERMISSIONS}:
        app_config = apps.get_app_config(app_label)
        historical = getattr(app_config, 'models_module', None) is None
        if historical:
            app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        if historical:
            app_config.models_module = None

    Group = apps.get_model('core', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    User = apps.get_model('core', 'User')

    group = Group.objects.filter(is_default=True).first() or Group.objects.filter(name=DEFAULT_GROUP_NAME).first()
    if group is None:
        group = Group.objects.create(name=DEFAULT_GROUP_NAME, slug=DEFAULT_GROUP_SLUG, is_default=True)
    elif not group.is_default:
        group.is_default = True
        group.save(update_fields=['is_default'])

    for app_label, codename in BASELINE_PERMISSIONS:
        group.permissions.add(*Permission.objects.filter(content_type__app_label=app_label, codename=codename))

    for user in User.objects.filter(groups__isnull=True):
        user.groups.add(group)


class Migration(migrations.Migration):
    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('contenttypes', '0002_remove_content_type_name'),
        ('core', '0006_task'),
        ('uploader', '0002_uploaded_by'),
    ]

    operations = [
        migrations.RunPython(ensure_default_group, migrations.RunPython.noop),
    ]
