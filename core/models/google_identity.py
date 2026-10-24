from django.conf import settings
from django.db import models


class GoogleIdentity(models.Model):
    """Vínculo entre um usuário do Fluxo e uma conta Google.

    A chave é o `sub` do Google (identificador imutável da conta); e-mail é só atributo de perfil e pode
    mudar. Um `sub` pertence a um único usuário e cada usuário tem no máximo uma conta Google vinculada.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, related_name='google_identity', on_delete=models.CASCADE)
    google_sub = models.CharField(max_length=255)
    email = models.EmailField(max_length=255, blank=True, help_text='Último e-mail informado pelo Google.')
    # Foto da conta Google copiada para a foto de perfil. Guardar qual imagem veio do Google permite atualizá-la
    # quando o Google muda, sem nunca trocar uma foto que a pessoa enviou no Fluxo.
    picture_url = models.URLField(max_length=2048, blank=True, help_text='URL da última foto importada do Google.')
    picture = models.ForeignKey('uploader.Image', related_name='+', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Conta Google vinculada'
        verbose_name_plural = 'Contas Google vinculadas'
        constraints = [models.UniqueConstraint(fields=['google_sub'], name='uq_google_identity_sub')]

    def __str__(self):
        return f'{self.user} ↔ Google {self.email or self.google_sub}'
