from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from core.models import Comment, Task

from .helpers import client_for, make_project, make_user

COMMENTS = '/api/comments/'


def comment_url(comment):
    return f'{COMMENTS}{comment.pk}/'


class CommentAPITestCase(APITestCase):
    def setUp(self):
        self.owner = make_user('dono')
        self.admin = make_user('admin')
        self.author = make_user('autor')
        self.member = make_user('membro')
        self.outsider = make_user('externo')
        self.project = make_project(self.owner, admins=[self.admin], members=[self.author, self.member])
        self.other_project = make_project(self.outsider, name='Outro projeto')
        self.task = Task.objects.create(project=self.project, column=self.project.columns.first(), title='Cartaz')
        self.other_task = Task.objects.create(
            project=self.other_project, column=self.other_project.columns.first(), title='Alheia'
        )
        self.comment = Comment.objects.create(task=self.task, author=self.author, body='Primeiro comentário')

    def bodies(self, response):
        return [item['body'] for item in response.data['results']]


class AuthenticationTests(CommentAPITestCase):
    def test_endpoints_require_authentication(self):
        anonymous = APIClient()
        url = comment_url(self.comment)
        for method, path in (('get', COMMENTS), ('post', COMMENTS), ('get', url), ('patch', url), ('delete', url)):
            with self.subTest(method=method, path=path):
                self.assertEqual(getattr(anonymous, method)(path).status_code, 401)


class ListAndDetailTests(CommentAPITestCase):
    def test_comments_are_listed_oldest_first_with_author(self):
        later = Comment.objects.create(task=self.task, author=self.member, body='Mais novo')
        earlier = Comment.objects.create(task=self.task, author=self.owner, body='Mais antigo')
        Comment.objects.filter(pk=earlier.pk).update(created_at=timezone.now() - timedelta(days=2))
        Comment.objects.filter(pk=self.comment.pk).update(created_at=timezone.now() - timedelta(days=1))
        Comment.objects.filter(pk=later.pk).update(created_at=timezone.now())

        response = client_for(self.member).get(COMMENTS, {'task': self.task.pk})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.bodies(response), ['Mais antigo', 'Primeiro comentário', 'Mais novo'])
        first = response.data['results'][1]
        self.assertEqual((first['author'], first['author_name']), (self.author.pk, self.author.name))
        self.assertIn('created_at', first)

    def test_comments_of_other_projects_are_isolated(self):
        Comment.objects.create(task=self.other_task, author=self.outsider, body='Alheio')

        self.assertEqual(self.bodies(client_for(self.member).get(COMMENTS)), ['Primeiro comentário'])
        self.assertEqual(client_for(self.member).get(COMMENTS, {'task': self.other_task.pk}).data['results'], [])

    def test_comment_of_deleted_user_shows_null_author(self):
        self.author.delete()

        item = client_for(self.member).get(COMMENTS).data['results'][0]

        self.assertEqual((item['author'], item['author_name'], item['body']), (None, None, 'Primeiro comentário'))

    def test_member_sees_detail(self):
        response = client_for(self.member).get(comment_url(self.comment))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['body'], 'Primeiro comentário')

    def test_outsider_gets_404(self):
        self.assertEqual(client_for(self.outsider).get(comment_url(self.comment)).status_code, 404)


class CreateCommentTests(CommentAPITestCase):
    def post(self, user=None, **payload):
        return client_for(user or self.member).post(COMMENTS, {'task': self.task.pk, 'body': 'Feito!', **payload})

    def test_any_member_comments_as_themselves(self):
        for user in (self.owner, self.admin, self.member):
            with self.subTest(user=user.email):
                response = self.post(user)
                self.assertEqual(response.status_code, 201)
                comment = Comment.objects.get(pk=response.data['id'])
                self.assertEqual((comment.author, comment.task, comment.body), (user, self.task, 'Feito!'))

    def test_author_in_payload_is_ignored(self):
        response = self.post(author=self.owner.pk)

        self.assertEqual(Comment.objects.get(pk=response.data['id']).author, self.member)

    def test_empty_body_is_rejected(self):
        for body in ('', '   ', '\n\t'):
            with self.subTest(body=repr(body)):
                response = self.post(body=body)
                self.assertEqual(response.status_code, 400)
                self.assertIn('body', response.data)

    def test_too_long_body_is_rejected(self):
        response = self.post(body='a' * 5001)

        self.assertEqual(response.status_code, 400)
        self.assertIn('body', response.data)

    def test_outsider_gets_404(self):
        self.assertEqual(self.post(self.outsider).status_code, 404)

    def test_task_of_another_project_gets_404(self):
        response = client_for(self.member).post(COMMENTS, {'task': self.other_task.pk, 'body': 'Invasão'})

        self.assertEqual(response.status_code, 404)
        self.assertFalse(Comment.objects.filter(task=self.other_task).exists())


class EditCommentTests(CommentAPITestCase):
    def patch(self, user, **payload):
        return client_for(user).patch(comment_url(self.comment), payload)

    def test_author_edits_own_comment(self):
        before = self.comment.updated_at

        response = self.patch(self.author, body='Comentário corrigido')

        self.assertEqual(response.status_code, 200)
        self.comment.refresh_from_db()
        self.assertEqual(self.comment.body, 'Comentário corrigido')
        self.assertGreater(self.comment.updated_at, before)

    def test_others_cannot_edit_even_owner_and_admin(self):
        for user in (self.member, self.admin, self.owner):
            with self.subTest(user=user.email):
                self.assertEqual(self.patch(user, body='Hackeado').status_code, 403)
        self.comment.refresh_from_db()
        self.assertEqual(self.comment.body, 'Primeiro comentário')

    def test_author_and_task_cannot_be_changed(self):
        other_task = Task.objects.create(project=self.project, column=self.project.columns.first(), title='Outra')

        self.patch(self.author, author=self.owner.pk)
        response = self.patch(self.author, task=other_task.pk)

        self.assertEqual(response.status_code, 400)
        self.comment.refresh_from_db()
        self.assertEqual((self.comment.author, self.comment.task), (self.author, self.task))

    def test_empty_body_is_rejected(self):
        response = self.patch(self.author, body='  ')

        self.assertEqual(response.status_code, 400)
        self.assertIn('body', response.data)

    def test_outsider_gets_404(self):
        self.assertEqual(self.patch(self.outsider, body='X').status_code, 404)


class DeleteCommentTests(CommentAPITestCase):
    def delete(self, user, comment=None):
        return client_for(user).delete(comment_url(comment or self.comment))

    def test_author_deletes_own_comment(self):
        self.assertEqual(self.delete(self.author).status_code, 204)
        self.assertFalse(Comment.objects.filter(pk=self.comment.pk).exists())

    def test_owner_and_admin_can_moderate(self):
        for user in (self.owner, self.admin):
            with self.subTest(user=user.email):
                comment = Comment.objects.create(task=self.task, author=self.author, body='Moderar')
                self.assertEqual(self.delete(user, comment).status_code, 204)

    def test_other_member_cannot_delete(self):
        self.assertEqual(self.delete(self.member).status_code, 403)
        self.assertTrue(Comment.objects.filter(pk=self.comment.pk).exists())

    def test_comment_of_deleted_user_can_be_moderated(self):
        self.author.delete()

        self.assertEqual(self.delete(self.member).status_code, 403)
        self.assertEqual(self.delete(self.owner).status_code, 204)

    def test_outsider_gets_404(self):
        self.assertEqual(self.delete(self.outsider).status_code, 404)
