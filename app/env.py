import os

TRUE_VALUES = {'1', 'true', 'yes', 'on'}


def env_bool(name, default=False):
    """Lê uma variável de ambiente como booleano ('True', '1', 'yes'...)."""
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in TRUE_VALUES


def env_list(name, default=''):
    """Lê uma variável de ambiente separada por vírgulas como lista."""
    value = os.getenv(name, default)

    return [item.strip() for item in value.split(',') if item.strip()]
