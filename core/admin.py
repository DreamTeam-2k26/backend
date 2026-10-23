from django import forms
from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import ReadOnlyPasswordHashField
from django.contrib.auth.models import Group as AuthGroup
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from core.models import Group, User

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
