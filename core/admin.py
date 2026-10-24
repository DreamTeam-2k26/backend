from django import forms
from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import ReadOnlyPasswordHashField
from django.contrib.auth.models import Group as AuthGroup
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from core.models import (
    ChecklistItem,
    Column,
    Comment,
    File,
    Folder,
    GoogleIdentity,
    Group,
    Invite,
    Meeting,
    Membership,
    Project,
    SupportLink,
    Task,
    User,
)

# O Group nativo do Django sai do admin para não haver dois painéis de grupos.
try:
    admin.site.unregister(AuthGroup)
except admin.sites.NotRegistered:
    pass


class UserCreationForm(forms.ModelForm):
    """Formulário de criação de usuário no admin (sem username)."""

    password1 = forms.CharField(label='Senha', widget=forms.PasswordInput)
    password2 = forms.CharField(label='Confirmação de senha', widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ('email', 'name')

    def clean_password2(self):
        password1 = self.cleaned_data.get('password1')
        password2 = self.cleaned_data.get('password2')

        if password1 and password2 and password1 != password2:
            raise ValidationError('As senhas não coincidem.')

        validate_password(password2, self.instance)

        return password2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password1'])

        if commit:
            user.save()

        return user


class UserChangeForm(forms.ModelForm):
    """Formulário de edição de usuário no admin (senha somente leitura)."""

    password = ReadOnlyPasswordHashField(label='Senha')

    class Meta:
        model = User
        fields = (
            'email',
            'name',
            'password',
            'is_active',
            'is_staff',
            'is_superuser',
            'groups',
            'user_permissions',
            'last_login',
        )


class UserAdmin(BaseUserAdmin):
    form = UserChangeForm
    add_form = UserCreationForm

    list_display = ('email', 'name', 'is_active', 'is_staff', 'is_superuser')
    list_filter = ('is_active', 'is_staff', 'is_superuser', 'groups')
    search_fields = ('email', 'name')
    ordering = ('email',)
    filter_horizontal = ('groups', 'user_permissions')

    fieldsets = (
        (None, {'fields': ('email', 'name', 'password')}),
        ('Foto de perfil', {'fields': ('profile_picture',)}),
        ('Permissões', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Datas importantes', {'fields': ('last_login',)}),
    )
    add_fieldsets = ((None, {'classes': ('wide',), 'fields': ('email', 'name', 'password1', 'password2')}),)

    def get_readonly_fields(self, request, obj=None):
        """Conceder privilégio é exclusivo de superusuário, também no admin."""
        readonly = super().get_readonly_fields(request, obj)

        if request.user.is_superuser:
            return readonly

        return (*readonly, 'is_staff', 'is_superuser', 'groups', 'user_permissions')


@admin.register(Group)
class CoreGroupAdmin(BaseGroupAdmin):
    """Admin dos grupos (tipos de usuário)."""

    list_display = ('name', 'slug', 'is_default', 'created_at')
    search_fields = ('name', 'slug', 'description')
    list_filter = ('is_default',)
    ordering = ('name',)

    def get_readonly_fields(self, request, obj=None):
        """Permissões concedem acesso: só superusuários podem alterá-las."""
        readonly = super().get_readonly_fields(request, obj)

        if request.user.is_superuser:
            return readonly

        return (*readonly, 'permissions')


admin.site.register(User, UserAdmin)


class ProjectMembershipInline(admin.TabularInline):
    model = Membership
    extra = 0
    raw_id_fields = ['user']


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ['name', 'created_by', 'created_at', 'updated_at']
    search_fields = ['name']
    raw_id_fields = ['created_by']
    inlines = [ProjectMembershipInline]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ('project', 'user', 'role', 'joined_at')
    list_filter = ('role',)
    search_fields = ('project__name', 'user__email')
    ordering = ('project', 'joined_at')
    raw_id_fields = ('project', 'user')


@admin.register(Invite)
class InviteAdmin(admin.ModelAdmin):
    list_display = ('project', 'kind', 'invited_user', 'role', 'status', 'expires_at', 'created_at')
    list_filter = ('kind', 'status', 'role')
    search_fields = ('project__name', 'invited_user__email')
    ordering = ('-created_at',)
    raw_id_fields = ('project', 'invited_user', 'invited_by')
    readonly_fields = ('token', 'created_at', 'responded_at')


@admin.register(Column)
class ColumnAdmin(admin.ModelAdmin):
    list_display = ('name', 'project', 'category', 'position')
    list_filter = ('category',)
    search_fields = ('name', 'project__name')
    ordering = ('project', 'position')
    raw_id_fields = ('project',)


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'project', 'column', 'priority', 'assignee', 'due_date', 'completed_at')
    list_filter = ('priority', 'column__category')
    search_fields = ('title', 'project__name', 'assignee__email')
    ordering = ('project', 'column__position', 'position')
    raw_id_fields = ('project', 'column', 'assignee', 'created_by')
    readonly_fields = ('completed_at', 'created_at', 'updated_at')


@admin.register(ChecklistItem)
class ChecklistItemAdmin(admin.ModelAdmin):
    list_display = ('text', 'task', 'is_done', 'position')
    list_filter = ('is_done',)
    search_fields = ('text', 'task__title')
    ordering = ('task', 'position')
    raw_id_fields = ('task',)


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ('task', 'author', 'created_at', 'updated_at')
    search_fields = ('body', 'task__title', 'author__email')
    ordering = ('-created_at',)
    raw_id_fields = ('task', 'author')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(SupportLink)
class SupportLinkAdmin(admin.ModelAdmin):
    list_display = ('title', 'project', 'task', 'url', 'created_by', 'created_at')
    search_fields = ('title', 'url', 'project__name', 'task__title')
    ordering = ('-created_at',)
    raw_id_fields = ('project', 'task', 'created_by')


@admin.register(Meeting)
class MeetingAdmin(admin.ModelAdmin):
    list_display = ('title', 'project', 'starts_at', 'ends_at', 'created_by')
    list_filter = ('starts_at',)
    search_fields = ('title', 'description', 'project__name')
    ordering = ('-starts_at',)
    raw_id_fields = ('project', 'created_by')


@admin.register(Folder)
class FolderAdmin(admin.ModelAdmin):
    list_display = ('name', 'project', 'parent', 'created_by', 'created_at')
    search_fields = ('name', 'project__name')
    ordering = ('project', 'name')
    raw_id_fields = ('project', 'parent', 'created_by')


@admin.register(File)
class FileAdmin(admin.ModelAdmin):
    list_display = ('name', 'project', 'folder', 'size', 'mime_type', 'uploaded_by', 'uploaded_at')
    list_filter = ('mime_type',)
    search_fields = ('name', 'project__name')
    ordering = ('-uploaded_at',)
    raw_id_fields = ('project', 'folder', 'uploaded_by')
    readonly_fields = ('file', 'size', 'mime_type', 'uploaded_at')


@admin.register(GoogleIdentity)
class GoogleIdentityAdmin(admin.ModelAdmin):
    list_display = ('user', 'email', 'created_at', 'last_login_at')
    search_fields = ('user__email', 'email', 'google_sub')
    ordering = ('-created_at',)
    raw_id_fields = ('user',)
    # O vínculo é criado só pelo fluxo autenticado da API; aqui dá para consultar ou remover.
    readonly_fields = ('user', 'google_sub', 'email', 'created_at', 'last_login_at')
