from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import AccessToken


class JWTAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        scope['user'] = await self.get_user(scope)

        return await super().__call__(
            scope,
            receive,
            send,
        )

    @database_sync_to_async
    def get_user(self, scope):
        User = get_user_model()

        try:
            token = self._get_token(scope)

            if not token:
                return AnonymousUser()

            access_token = AccessToken(token)
            user_id = access_token.get('user_id')

            if not user_id:
                return AnonymousUser()

            return User.objects.get(id=user_id, is_active=True)

        except (
            InvalidToken,
            TokenError,
            User.DoesNotExist,
            KeyError,
            ValueError,
        ):
            return AnonymousUser()

    @staticmethod
    def _get_token(scope):
        query_string = scope.get('query_string', b'').decode()

        query_params = parse_qs(query_string)
        token = query_params.get('token')

        if not token:
            return None
        return token[0]


def JWTAuthMiddlewareStack(inner):
    return JWTAuthMiddleware(inner)
