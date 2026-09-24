"""
Sample content for `manage.py seed_sample_content`: four slide-based training courses with
their quizzes, five simulated phishing emails, two landing pages, catalog entries and draft
campaigns. All of it is fictional and uses no real company's name or branding.

Course text is plain text: a blank line starts a paragraph, "- " lines are bullets.
Each quiz question is (text, [choices], index_of_correct_choice).
"""

# --- training courses --------------------------------------------------------------------

COURSES = [
    {
        "title": "Spotting Phishing Emails",
        "description": "How phishing works, the warning signs to look for, and how to check a message before you act on it.",
        "minutes": 8,
        "pass_mark": 80,
        "slides": [
            ("Why phishing matters", "Most security breaches start with a message that looks routine.\n\n- Over 90% of successful attacks begin with a phishing email or message\n- Attackers target people because people are easier to fool than firewalls\n- Anyone can be targeted: finance, engineering, HR, executives, new hires", "You are the first line of defence, and the most useful one."),
            ("What phishing looks like", "Phishing is a message designed to make you act before you think.\n\n- Email is the classic route, but also text messages, chat, phone calls and QR codes\n- The goal is usually one of three things: steal a password, trick you into opening malware, or get money moved", ""),
            ("Red flag 1: the sender", "Check who the message is really from, not just the display name.\n\n- The display name says \"IT Support\" but the address is a random or look-alike domain\n- Small changes: rnicrosoft instead of microsoft, or a .co instead of .com\n- A message that claims to be internal but comes from an outside address", "Look at the address, not the name."),
            ("Red flag 2: urgency and pressure", "Attackers want you to skip the thinking step.\n\n- \"Your account will be closed in 24 hours\"\n- \"Pay this invoice today or we stop service\"\n- \"The CEO needs this done now, don't tell anyone\"\n\nReal requests rarely need you to break normal process.", "Urgency is a tactic. Slow down when you feel rushed."),
            ("Red flag 3: links and attachments", "Do not click first and ask later.\n\n- Hover over a link (or press and hold on mobile) to see where it really goes\n- Be suspicious of shortened links and of pages that ask you to sign in\n- Treat unexpected attachments as unsafe, especially .zip, .html, and Office files asking to \"enable macros\"", "If in doubt, go to the site yourself. Type the address or use a saved bookmark."),
            ("Red flag 4: the ask", "Look at what the message wants you to do.\n\n- Sign in to \"verify\" or \"reset\" something you did not request\n- Share a password, a code, or a personal detail\n- Buy gift cards, change bank details, or approve a payment\n\nLegitimate teams never ask for your password.", ""),
            ("Check before you act", "You have three easy checks. Use them whenever a message asks for action.\n\n- Pause: does this fit how this person or team normally contacts me?\n- Verify: confirm through a different channel, such as a call to a number you already have\n- Report: if it still feels off, report it rather than deleting it", "Verify using a different channel from the one the message arrived on."),
            ("Report it", "Reporting is the most useful thing you can do, even if you are not sure.\n\n- Use the report button or the Report an email page in this portal\n- Reporting a suspicious message protects your colleagues, who may have received the same one\n- You will not be in trouble for reporting a real email by mistake", "Reporting helps everyone. When unsure, report."),
        ],
        "questions": [
            ("An email from \"IT Support\" asks you to confirm your password within 24 hours. What is the strongest warning sign?", ["The email has a subject line", "It combines urgency with a request for your password", "It was sent during work hours", "It is addressed to you by name"], 1),
            ("Before clicking a link in an unexpected email, what should you do?", ["Click it quickly to see what it is", "Forward it to a colleague and ask them to click", "Hover to check where it really goes, or go to the site yourself", "Reply to the sender asking if it is safe"], 2),
            ("The sender name shows your manager, but the address is from a free webmail service. What does this suggest?", ["A normal message sent from home", "A possible impersonation", "An email that was delayed", "Nothing worth checking"], 1),
            ("Which attachment is the biggest concern when it arrives unexpectedly?", ["A plain text file from a known colleague you were expecting", "A .zip or .html file asking you to open it right away", "A calendar invite from your team", "A PDF you asked for"], 1),
            ("You are not sure whether an email is real. What is the best next step?", ["Delete it and move on", "Reply and ask", "Verify by another channel and report it", "Click the link, then decide"], 2),
        ],
    },
    {
        "title": "Passwords and Account Security",
        "description": "Build strong passwords, avoid reuse, and protect your accounts from takeover.",
        "minutes": 6,
        "pass_mark": 80,
        "slides": [
            ("Your account is a target", "Attackers use stolen or guessed passwords to get in quietly.\n\n- A stolen password from one website is tried on many others\n- Once inside your mailbox, an attacker can reset almost anything else\n- Accounts are often abused for weeks before anyone notices", ""),
            ("What makes a strong password", "Length beats complexity.\n\n- Aim for a passphrase of four or more unrelated words\n- Avoid names, birthdays, keyboard patterns and anything on a social profile\n- Longer is better; substituting @ for a is not", "A long passphrase is stronger and easier to remember than P@ssw0rd1."),
            ("Never reuse passwords", "If one site is breached, every account that shares the password is at risk.\n\n- Use a different password for every account\n- Work and personal passwords should never overlap\n- A password manager makes this practical", ""),
            ("Use a password manager", "You do not need to remember dozens of passwords.\n\n- A password manager creates and stores a unique password for each account\n- It fills passwords only on the real site, which is a quiet phishing defence\n- Protect the manager itself with a strong passphrase", "If a page will not auto-fill, stop and check the address."),
            ("Passwords stay private", "Nobody who works with you needs your password.\n\n- IT will never ask for it, by email, chat or phone\n- Do not share accounts or write passwords on notes\n- If you think a password is exposed, change it and tell the security team", ""),
            ("If something looks wrong", "Act quickly, and do not feel embarrassed.\n\n- Change the password, starting with email\n- Tell the security team straight away\n- Check for unfamiliar sign-ins or forwarding rules in your mailbox", "Speed matters more than being certain."),
        ],
        "questions": [
            ("Which is the strongest password choice?", ["Summer2024!", "A four-word passphrase of unrelated words", "Your pet's name with a number", "Qwerty123"], 1),
            ("A colleague from IT emails asking for your password to fix a problem. What do you do?", ["Send it, they are IT", "Send half of it", "Refuse and report the message", "Ask them to call you and then send it"], 2),
            ("Why is reusing a password on several sites risky?", ["It is slower to type", "One breach exposes all the accounts that share it", "Websites block reused passwords", "It is not risky"], 1),
            ("A password manager will not auto-fill your login on a page. What might that mean?", ["The manager is broken", "The site may be a fake, so check the address", "You must retype it every time", "Nothing at all"], 1),
        ],
    },
    {
        "title": "Invoice and Payment Fraud",
        "description": "Recognise fake invoices, changed bank details and executive impersonation before money moves.",
        "minutes": 7,
        "pass_mark": 80,
        "slides": [
            ("Follow the money", "Business email compromise is one of the costliest kinds of fraud.\n\n- Attackers pose as a supplier, a colleague or an executive\n- They ask for a payment, a changed bank account, or gift cards\n- There is often no link or attachment, so filters miss it", ""),
            ("The fake invoice", "It looks ordinary. That is the point.\n\n- The amount, format and supplier name look familiar\n- The bank details or the \"pay now\" link are different\n- A reminder message adds pressure: \"final notice\", \"overdue\"", "Always match an invoice to a purchase order you know about."),
            ("Changed bank details", "A request to change where money goes is a classic warning sign.\n\n- The message claims the supplier has a new bank\n- It comes from an address that is almost right\n- Someone asks you to keep it quiet or act today", "Confirm any bank-detail change by phoning a number you already have on file."),
            ("Executive impersonation", "A message that appears to be from a senior person asks for a quick favour.\n\n- \"Are you at your desk?\" is often the opening line\n- Then a request for gift cards, a transfer or personal details\n- They say they are in a meeting and can't talk", "Senior people do not ask for secrecy or bypassed process."),
            ("Your safe process", "Build friction into the steps that move money.\n\n- Follow your normal approval process, every time, no matter who asks\n- Verify by a second channel\n- Two people should be involved in changing payment details or approving large payments", ""),
            ("If you suspect it", "Speed is important, but so is calm.\n\n- Do not pay, and do not reply to the message\n- Report it to the security team and to finance\n- If money has already moved, tell finance immediately. Banks can sometimes recall it, but only quickly", "Report early. Recovery odds fall by the hour."),
        ],
        "questions": [
            ("A supplier emails to say their bank details have changed and asks you to update them. What should you do?", ["Update them, since it is from the supplier", "Confirm by calling a known number before changing anything", "Reply asking them to confirm by email", "Pay the next invoice to the new account to test it"], 1),
            ("Your \"CEO\" emails asking you to buy gift cards urgently and keep it quiet. This is:", ["A normal request", "Likely fraud, so report it", "Fine if you use the company card", "Fine if they sound stressed"], 1),
            ("Which control best protects against payment fraud?", ["Paying quickly to avoid late fees", "A second person approving changes to payment details", "Trusting emails that look professional", "Only checking invoices over a certain size"], 1),
            ("You paid an invoice and now suspect it was fake. What next?", ["Wait and see if the supplier chases", "Tell finance and the security team immediately", "Delete the email", "Ask the sender to confirm"], 1),
        ],
    },
    {
        "title": "Reporting and Responding to Incidents",
        "description": "What to do when you spot a suspicious message, or if you have clicked something you shouldn't have.",
        "minutes": 5,
        "pass_mark": 80,
        "slides": [
            ("You will see something eventually", "Every employee meets a suspicious message sooner or later. What happens next matters more than the mistake.\n\n- Early reports stop attacks from spreading\n- The security team would much rather hear about a false alarm than miss a real attack", ""),
            ("If you spot a suspicious message", "Do not engage with it.\n\n- Do not click links or open attachments\n- Do not reply or forward it to colleagues\n- Report it using the report button or the Report an email page", "Reporting takes under a minute and helps protect everyone."),
            ("If you already clicked", "Do not panic, and do not hide it. Speed helps.\n\n- Disconnect from the network if you opened a suspicious file\n- Do not enter any more information\n- Tell the security team straight away, with what you saw\n- Change your password if you typed one in", "There is no blame. What matters is how fast you tell us."),
            ("What happens after you report", "The security team looks at every report.\n\n- They check whether others received the same message\n- They block the sender or the link where needed\n- Your report is recorded, and helps keep the company safe", ""),
        ],
        "questions": [
            ("You get a suspicious email. What do you do first?", ["Forward it to your team to warn them", "Report it without clicking or replying", "Reply to ask if it's real", "Ignore it"], 1),
            ("You clicked a link and entered your password on a page that now looks wrong. What next?", ["Say nothing, and hope it's fine", "Tell the security team immediately and change your password", "Wait until Monday", "Delete your browser history"], 1),
            ("Will you get in trouble for reporting a message that turns out to be harmless?", ["Yes", "No, reports are welcomed, whether or not the message is real", "Only if it happens repeatedly", "Only if you are in finance"], 1),
        ],
    },
]

# --- phishing simulation emails ------------------------------------------------------------
# Composed messages (apps.campaigns.EmailDraft.body_html), the same HTML the composer produces.
# {{.FirstName}} is Gophish's merge field; every link and button is the tracked link.
# Everything is fictional; no real brand is imitated. "{hero}" is replaced by the sample banner.

BUTTON = '<p><a href="{{.URL}}" data-button="1">%s</a></p>'

EMAIL_TEMPLATES = [
    {
        "name": "Sample: Password expiry notice",
        "subject": "Action required: your password expires in 24 hours",
        "layout": "corporate", "brand_name": "IT Service Desk",
        "body_html": (
            "<h2>Your password expires soon</h2><p>Hi {{.FirstName}},</p>"
            "<p>Your network password will expire in <b>24 hours</b>. To avoid losing access to email, shared files and the VPN, "
            "confirm your account now. It only takes a minute.</p>"
            "<ul><li>Keep your current password with one click</li><li>No IT ticket needed</li><li>Applies to all staff accounts</li></ul>"
            + BUTTON % "Keep my password"
            + "<p>IT Service Desk<br>This is an automated message. Please do not reply.</p>"
        ),
        "category": "Credential", "difficulty": "easy", "landing": "Sample: Sign-in page",
    },
    {
        "name": "Sample: Shared document",
        "subject": "Q3 budget review: document shared with you",
        "layout": "minimal", "brand_name": "",
        "body_html": (
            "<p>Hi {{.FirstName}},</p><p>I've shared the Q3 budget review with you. Could you add your comments before Friday? "
            "I need everyone's numbers to finalise it.</p><p><b>Q3 Budget Review.xlsx</b> (2.4 MB)</p>"
            + BUTTON % "Open document"
            + "<p>Thanks,<br>Jordan<br><i>Finance</i></p>"
        ),
        "category": "Document sharing", "difficulty": "medium", "landing": "Sample: Sign-in page",
    },
    {
        "name": "Sample: Parcel delivery",
        "subject": "We couldn't deliver your parcel",
        "layout": "corporate", "brand_name": "Parcel Services",
        "body_html": (
            "{hero}<h2>Delivery attempt unsuccessful</h2><p>Hello {{.FirstName}},</p>"
            "<p>We tried to deliver your parcel today but nobody was available to sign for it. Please rearrange delivery within "
            "48 hours or the parcel will be returned to the sender.</p>"
            "<ul><li>Tracking reference: 7731-2208</li><li>Delivery attempt: 1 of 2</li><li>Redelivery fee: none</li></ul>"
            + BUTTON % "Rearrange delivery"
        ),
        "hero": "Sample banner (blue)",
        "category": "Delivery", "difficulty": "easy", "landing": "Sample: Sign-in page",
    },
    {
        "name": "Sample: Benefits enrolment",
        "subject": "Benefits enrolment closes Friday: confirm your choices",
        "layout": "corporate", "brand_name": "People Team",
        "body_html": (
            "{hero}<h2>Open enrolment closes this Friday</h2><p>Hi {{.FirstName}},</p>"
            "<p>Open enrolment for company benefits closes this Friday. Employees who don't confirm their choices will be moved "
            "to the default plan for the year.</p>"
            "<ul><li>Health and dental options</li><li>Pension contribution changes</li><li>Flexible spending</li></ul>"
            + BUTTON % "Confirm my benefits"
        ),
        "hero": "Sample banner (teal)",
        "category": "HR", "difficulty": "medium", "landing": "Sample: Account verification",
    },
    {
        "name": "Sample: Overdue invoice",
        "subject": "Final notice: invoice #48213 is overdue",
        "layout": "alert", "brand_name": "Accounts Receivable",
        "body_html": (
            "<h2>Final notice: payment overdue</h2><p>Dear {{.FirstName}},</p>"
            "<p>Our records show invoice <b>#48213</b> remains unpaid. To avoid a suspension of service, please review the invoice "
            "and arrange payment today.</p>"
            "<ul><li>Invoice: #48213</li><li>Amount due: 4,860.00</li><li>Status: 21 days overdue</li></ul>"
            + BUTTON % "View invoice"
        ),
        "category": "Finance", "difficulty": "hard", "landing": "Sample: Document viewer",
    },
]

# --- landing pages ------------------------------------------------------------------------
# Fields of the landing-page builder (apps.campaigns.LandingDraft). The compiled page is always a
# native <form> with a real password input (invariant #4); the adapter forces capture_passwords off.

LANDING_PAGES = [
    {
        "name": "Sample: Sign-in page", "layout": "signin", "brand_name": "Company Portal", "accent_color": "#0b5fff",
        "heading": "Sign in to continue", "subtext": "Use your work account to access this resource.",
        "username_label": "Work email", "ask_password": True, "button_label": "Sign in",
        "footer_note": "Protected by Company Portal. Need help? Contact the IT Service Desk.",
    },
    {
        "name": "Sample: Document viewer", "layout": "document", "brand_name": "Secure Documents", "accent_color": "#c62828",
        "heading": "Invoice 48213.pdf", "subtext": "Confirm your work email to open this document.",
        "username_label": "Work email", "ask_password": True, "button_label": "Open document",
        "footer_note": "This link is private. Do not forward it.",
    },
    {
        "name": "Sample: Account verification", "layout": "verify", "brand_name": "Account Security", "accent_color": "#0f766e",
        "heading": "Verify your account", "subtext": "We noticed a sign-in from a new device. Confirm it was you to keep your account secure.",
        "username_label": "Work email", "ask_password": True, "button_label": "Verify",
        "footer_note": "If this wasn't you, you will be asked to reset your password.",
    },
]

# --- draft campaigns ----------------------------------------------------------------------
# (name, email template, landing page, course to assign on failure, status)

CAMPAIGNS = [
    ("Sample: Password expiry test", "Sample: Password expiry notice", "Sample: Sign-in page", "Passwords and Account Security", "draft"),
    ("Sample: Shared document test", "Sample: Shared document", "Sample: Sign-in page", "Spotting Phishing Emails", "draft"),
    ("Sample: Parcel delivery test", "Sample: Parcel delivery", "Sample: Sign-in page", "Spotting Phishing Emails", "draft"),
    ("Sample: Overdue invoice test", "Sample: Overdue invoice", "Sample: Document viewer", "Invoice and Payment Fraud", "pending_approval"),
]
