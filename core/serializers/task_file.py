from django.db import IntegrityError, transaction
from rest_framework.serializers import PrimaryKeyRelatedField, Serializer, ValidationError

from core.models import File, TaskFile

ALREADY_ATTACHED = 'Este arquivo já está anexado à tarefa.'


def attached_files(task):
    """Arquivos anexados à tarefa, na ordem em que foram anexados."""
    return (
        File.objects
        .filter(task_links__task=task)
        .select_related('uploaded_by')
        .order_by('task_links__attached_at', 'task_links__id')
    )


class TaskFileAttachSerializer(Serializer):
    """Anexa um arquivo que já existe no projeto à tarefa (context['task']), sem copiar o arquivo."""

    file = PrimaryKeyRelatedField(queryset=File.objects.none(), help_text='Id de um arquivo do mesmo projeto.')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Só arquivos do projeto da tarefa: id de outro projeto responde igual a id inexistente (não vaza nada).
        # Sem tarefa (ex.: geração do schema) continua sem aceitar nenhum arquivo.
        task = self.context.get('task')
        if task is not None:
            self.fields['file'].queryset = File.objects.filter(project=task.project_id)

    def validate_file(self, file):
        if TaskFile.objects.filter(task=self.context['task'], file=file).exists():
            raise ValidationError(ALREADY_ATTACHED)
        return file

    def create(self, validated_data):
        try:
            with transaction.atomic():
                return TaskFile.objects.create(
                    task=self.context['task'], file=validated_data['file'], attached_by=self.context['request'].user
                )
        except IntegrityError as error:
            # Dois cliques/requisições simultâneos: o UniqueConstraint barra o segundo.
            raise ValidationError({'file': ALREADY_ATTACHED}) from error
