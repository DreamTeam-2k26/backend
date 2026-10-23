from rest_framework.permissions import BasePermission, DjangoModelPermissions

from core.models import Membership


class ModelPermissions(DjangoModelPermissions):
    """Exige a permissão do grupo conforme a ação sobre o modelo da view.

    Diferente do ``DjangoModelPermissions`` padrão, a leitura também exige
    ``view_<model>``. Ações customizadas declaram suas permissões no viewset em
    ``action_permissions`` (ex.: ``{'add_users': ['core.change_group']}``).
    Superusuários passam automaticamente pelo ``ModelBackend``.
    """

    message = 'Seu grupo não permite executar esta ação.'

    perms_map = {
        'GET': ['%(app_label)s.view_%(model_name)s'],
        'HEAD': ['%(app_label)s.view_%(model_name)s'],
        'OPTIONS': [],
        'POST': ['%(app_label)s.add_%(model_name)s'],
        'PUT': ['%(app_label)s.change_%(model_name)s'],
        'PATCH': ['%(app_label)s.change_%(model_name)s'],
        'DELETE': ['%(app_label)s.delete_%(model_name)s'],
    }

    def has_permission(self, request, view):
        user = request.user

        if not (user and user.is_authenticated):
            return False

        # Métodos que a view não implementa continuam respondendo 405 (e não 403).
        if request.method not in getattr(view, 'allowed_methods', (request.method,)):
            return True

        action_permissions = getattr(view, 'action_permissions', {}).get(getattr(view, 'action', None))

        if action_permissions:
            return user.has_perms(action_permissions)

        return super().has_permission(request, view)


class SelfUserPermission(ModelPermissions):
    """Permite que o usuário leia e edite o próprio cadastro.

    Sem essa exceção, o autoatendimento exigiria ``core.view_user`` e
    ``core.change_user`` para todos os usuários. A exclusão continua exigindo
    ``core.delete_user``.
    """

    self_service_actions = frozenset({'retrieve', 'update', 'partial_update'})

    def has_permission(self, request, view):
        user = request.user

        if not (user and user.is_authenticated):
            return False

        if getattr(view, 'action', None) in self.self_service_actions:
            target_pk = view.kwargs.get('pk')

            if target_pk is not None and str(target_pk) == str(user.pk):
                return True

        return super().has_permission(request, view)


class ProjectRolePermission(BasePermission):
    """Editar projeto: dono ou administrador. Excluir: somente o dono.

    Usa o ``role`` que a ProjectViewSet anota em cada projeto (papel do usuário logado).
    Ler é livre para qualquer participante; quem não participa nem chega aqui (404).
    """

    message = 'Seu papel no projeto não permite executar esta ação.'

    def has_object_permission(self, request, view, obj):
        if view.action == 'destroy':
            return obj.role == Membership.Role.OWNER

        if view.action in {'update', 'partial_update'}:
            return obj.role in {Membership.Role.OWNER, Membership.Role.ADMIN}

        return True


class MembershipRolePermission(BasePermission):
    """Gestão de participantes pelo papel do usuário logado no projeto da participação.

    Alterar papel (inclusive transferir a propriedade): somente o dono.
    Remover: o dono remove admin ou membro; o admin remove somente membro.
    Usa o ``my_role`` que a MembershipViewSet anota em cada participação.
    Ler é livre para qualquer participante; quem não participa nem chega aqui (404).
    """

    message = 'Seu papel no projeto não permite executar esta ação.'

    def has_object_permission(self, request, view, obj):
        if view.action in {'update', 'partial_update'}:
            return obj.my_role == Membership.Role.OWNER

        if view.action == 'destroy':
            if obj.my_role == Membership.Role.ADMIN:
                return obj.role == Membership.Role.MEMBER
            return obj.my_role == Membership.Role.OWNER

        return True


class InvitePermission(BasePermission):
    """Convites: dono/admin do projeto gerenciam (ver, revogar); só o destinatário aceita ou recusa.

    Usa o ``my_role`` que a InviteViewSet anota em cada convite (papel do usuário logado no projeto).
    Quem não participa do projeto e não é o destinatário nem chega aqui (404).
    """

    message = 'Você não tem permissão para esta ação neste convite.'

    def has_object_permission(self, request, view, obj):
        is_manager = obj.my_role in {Membership.Role.OWNER, Membership.Role.ADMIN}
        is_invited_user = obj.invited_user_id == request.user.pk

        if view.action in {'accept', 'decline'}:
            return is_invited_user

        if view.action == 'revoke':
            return is_manager

        return is_manager or is_invited_user


class IsSuperuser(BasePermission):
    """Restringe a ação a superusuários (operações que concedem privilégios)."""

    message = 'Apenas superusuários podem executar esta ação.'

    def has_permission(self, request, view):
        user = request.user

        return bool(user and user.is_authenticated and user.is_superuser)
