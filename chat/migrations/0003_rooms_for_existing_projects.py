from django.db import migrations

ADMIN_ROLES = ('owner', 'admin')


def create_rooms(apps, schema_editor):
    """Cria a sala de cada projeto que já existe, com os participantes atuais do projeto."""
    Project = apps.get_model('core', 'Project')
    ProjectMembership = apps.get_model('core', 'Membership')
    Room = apps.get_model('chat', 'Room')
    Membership = apps.get_model('chat', 'Membership')

    for project in Project.objects.filter(chat_room__isnull=True):
        room = Room.objects.create(name=project.name[:100], project=project, created_by_id=project.created_by_id)
        members = ProjectMembership.objects.filter(project=project, user__is_active=True)
        Membership.objects.bulk_create([
            Membership(room=room, user_id=member.user_id, is_admin=member.role in ADMIN_ROLES) for member in members
        ])


class Migration(migrations.Migration):
    dependencies = [('chat', '0002_room_project')]

    # Sem reversão de dados: desfazer o 0002 remove a coluna `project` e as salas ficam como salas avulsas.
    operations = [migrations.RunPython(create_rooms, migrations.RunPython.noop)]
