from django.urls import path

from . import views, views_apikeys

app_name = "manage"

urlpatterns = [
    path("", views.index, name="index"),

    path("campaigns/", views.campaigns, name="campaigns"),
    path("campaigns/new/", views.campaign_new, name="campaign-new"),
    path("campaigns/<int:pk>/edit/", views.campaign_edit, name="campaign-edit"),
    path("campaigns/<int:pk>/submit/", views.campaign_submit, name="campaign-submit"),
    path("campaigns/<int:pk>/approve/", views.campaign_approve, name="campaign-approve"),
    path("campaigns/<int:pk>/reject/", views.campaign_reject, name="campaign-reject"),
    path("campaigns/<int:pk>/launch/", views.campaign_launch, name="campaign-launch"),

    path("content/", views.content, name="content"),
    path("content/catalog/", views.catalog, name="catalog"),
    path("content/catalog/templates/new/", views.catalog_template_edit, name="catalog-template-new"),
    path("content/catalog/templates/<int:pk>/edit/", views.catalog_template_edit, name="catalog-template-edit"),
    path("content/catalog/smart-groups/new/", views.smart_group_edit, name="smart-group-new"),
    path("content/catalog/smart-groups/<int:pk>/edit/", views.smart_group_edit, name="smart-group-edit"),
    path("content/templates/new/", views.template_new, name="template-new"),
    path("content/pages/new/", views.page_new, name="page-new"),
    path("content/pages/<str:name>/preview/", views.page_preview, name="page-preview"),

    path("training/", views.training, name="training"),
    path("training/new/", views.module_edit, name="module-new"),
    path("training/<int:pk>/edit/", views.module_edit, name="module-edit"),
    path("training/<int:pk>/questions/add/", views.question_add, name="question-add"),
    path("training/<int:pk>/questions/<int:question_pk>/delete/", views.question_delete, name="question-delete"),

    path("training/policies/", views.policies, name="policies"),
    path("training/policies/new/", views.policy_edit, name="policy-new"),
    path("training/policies/<int:pk>/edit/", views.policy_edit, name="policy-edit"),

    path("employees/", views.employees, name="employees"),
    path("employees/new/", views.employee_edit, name="employee-new"),
    path("employees/import/", views.employee_import, name="employee-import"),
    path("employees/<int:pk>/edit/", views.employee_edit, name="employee-edit"),

    path("departments/", views.departments, name="departments"),
    path("departments/new/", views.department_edit, name="department-new"),
    path("departments/<int:pk>/edit/", views.department_edit, name="department-edit"),

    path("scheduled-reports/", views.schedules, name="schedules"),
    path("scheduled-reports/new/", views.schedule_edit, name="schedule-new"),
    path("scheduled-reports/<int:pk>/edit/", views.schedule_edit, name="schedule-edit"),

    path("reported/", views.reported, name="reported"),
    path("reported/<int:pk>/triage/", views.reported_triage, name="reported-triage"),

    path("api-keys/", views_apikeys.api_keys, name="api-keys"),
    path("api-keys/<int:pk>/revoke/", views_apikeys.api_key_revoke, name="api-key-revoke"),
]
