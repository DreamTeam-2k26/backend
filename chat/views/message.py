from django.http import FileResponse
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from chat import services

SECURITY_HEADERS = {
    'X-Content-Type-Options': 'nosniff',
    'Content-Security-Policy': "default-src 'none'; sandbox",
    'Cache-Control': 'private, max-age=3600',
}


class MessageFileView(APIView):
    """Download de um anexo, somente para participantes da sala da mensagem.

    O storage nunca é exposto: o caminho real do arquivo é aleatório e não aparece em nenhuma resposta.
    Só imagens reconhecidas pelo conteúdo são servidas inline; todo o resto vai como download
    (attachment) com tipo genérico, para o navegador nunca interpretar um arquivo enviado.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(summary='Baixar anexo de uma mensagem', responses={200: bytes})
    def get(self, request, pk):
        message = services.get_message_for(request.user, pk)

        if message is None or message.is_deleted or not message.file:
            raise NotFound('Arquivo não encontrado.')

        try:
            stream = message.file.open('rb')
        except (FileNotFoundError, OSError) as error:
            raise NotFound('Arquivo não encontrado.') from error

        inline = message.is_image
        response = FileResponse(
            stream,
            as_attachment=not inline,
            filename=message.file_name or 'arquivo',
            content_type=message.file_content_type if inline else 'application/octet-stream',
        )

        for header, value in SECURITY_HEADERS.items():
            response[header] = value

        return response
