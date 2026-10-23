from rest_framework.test import APITestCase


class SchemaTests(APITestCase):
    def test_openapi_schema_is_generated(self):
        response = self.client.get('/api/schema/')

        self.assertEqual(response.status_code, 200)
