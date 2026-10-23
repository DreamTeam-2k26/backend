from django.contrib.auth.models import GroupManager, Permission
from django.db import models
from django.utils.translation import gettext_lazy as _


class AbstractGroup(models.Model):
    """Base abstrata dos grupos (tipos de usuário) do projeto.

    O Django não possui um ``AbstractGroup`` nativo nem a setting
    ``AUTH_GROUP_MODEL``, então a base é criada aqui e o campo ``groups`` do
    usuário é sobrescrito para apontar para o modelo concreto ``Group``.
    """

    name = models.CharField(max_length=150, unique=True, verbose_name=_('nome'))
    slug = models.SlugField(max_length=150, unique=True, blank=True, verbose_name=_('slug'))
    description = models.TextField(blank=True, verbose_name=_('descrição'))
    is_default = models.BooleanField(default=False, verbose_name=_('grupo padrão'))
    permissions = models.ManyToManyField(
        Permission,
        blank=True,
        verbose_name=_('permissões'),
        # related_name evita clash com o campo homônimo do Group nativo do Django.
        related_name='core_groups',
        # related_query_name 'group' é exigido pelo ModelBackend, que resolve
        # as permissões do grupo com Permission.objects.filter(group__in=...).
        related_query_name='group',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('criado em'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('atualizado em'))

    objects = GroupManager()

    class Meta:
        abstract = True
        ordering = ['name']
        verbose_name = _('grupo')
        verbose_name_plural = _('grupos')

    def __str__(self):
        return self.name

    def natural_key(self):
        return (self.name,)


class Group(AbstractGroup):
    """Grupo concreto do projeto — representa um tipo de usuário."""

    class Meta(AbstractGroup.Meta):
        abstract = False
