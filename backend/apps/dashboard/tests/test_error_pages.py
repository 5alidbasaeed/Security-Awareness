import pytest
from django.template.loader import render_to_string
from django.test import override_settings

pytestmark = pytest.mark.django_db


@override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver"])
def test_unknown_urls_get_the_branded_404_not_the_default(client):
    response = client.get("/definitely-not-a-page/")
    assert response.status_code == 404
    assert "We can’t find that page" in response.content.decode()


def test_500_page_is_self_contained_and_needs_no_context():
    html = render_to_string("500.html")
    assert "Something went wrong" in html and "style=" not in html
