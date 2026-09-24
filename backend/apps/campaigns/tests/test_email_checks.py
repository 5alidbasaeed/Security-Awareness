"""Pre-flight advice for a composed email. Advisory only — never blocks saving."""

from apps.campaigns.email_checks import check_email


def levels(found):
    return {message for _, message in found}


def test_a_solid_email_has_nothing_to_flag():
    found = check_email(
        subject="Your monthly security digest",
        html="<p>Hi {{.FirstName}},</p><p>Here is a short update on this month's security news. We found nothing "
             "urgent, but wanted to remind everyone to keep reporting anything unusual you notice in your inbox, "
             "however small it may seem, since early reports genuinely help the team.</p>"
             '<p><a href="{{.URL}}" data-button="1">Read the full update</a></p>'
             '<img src="https://x/y.png" alt="A chart of this month\'s report count">',
        text="Hi, here is a short update on this month's security news. Read the full update.",
    )

    assert found == []


def test_flags_a_missing_subject_and_missing_link():
    found = check_email(subject="", html="<p>Hi</p>", text="Hi")

    messages = levels(found)
    assert any("subject is empty" in m for m in messages)
    assert any("no link or button" in m for m in messages)


def test_flags_shouty_subjects_and_spammy_words():
    found = levels(check_email(subject="FREE OFFER!! ACT NOW", html="<p>x</p><p><a href='{{.URL}}'>go</a></p>", text="x"))

    assert any("capital letters" in m for m in found)
    assert any("! or $" in m for m in found)
    assert any("spam filters" in m for m in found)


def test_flags_too_many_links():
    html = "".join(f'<p><a href="{{{{.URL}}}}">link {i}</a></p>' for i in range(5))

    found = levels(check_email(subject="S", html=html, text="x"))

    assert any("There are 5 links" in m for m in found)


def test_flags_image_heavy_messages_with_little_text():
    found = levels(check_email(subject="S", html='<img src="a.png" alt="a"><img src="b.png" alt="b">', text=""))

    assert any("Mostly pictures" in m for m in found)


def test_flags_no_personalisation_and_no_plain_text():
    found = levels(check_email(
        subject="Notice", html="<p>" + "This is a message with enough content to pass the length check. " * 3 + "</p>",
        text="",
    ))

    assert any("recipient's name" in m for m in found)
    assert any("no plain-text version" in m for m in found)


def test_never_raises_on_empty_input():
    assert check_email(subject="", html="", text="") is not None
