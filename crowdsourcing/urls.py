from __future__ import absolute_import
import logging

from django.conf.urls  import patterns, url, include

from .views import (allowed_actions,
                    embeded_survey_questions,
                    embeded_survey_report,
                    location_question_results,
                    location_question_map,
                    questions,
                    submissions,
                    submission,
                    submission_for_map,
                    survey_detail,
                    survey_completed,
                    #survey_report,
                    SurveyReportView,
                    )

try:
    import crowdsourcing.tastypiesupport
    tastypie_api = include(crowdsourcing.tastypiesupport)
except ImportError:
    logging.warn('no tastypie support available (API disabled)')
    from django.views.defaults import page_not_found
    tastypie_api = page_not_found

urlpatterns = patterns(
    "",
    url(r'^api/', tastypie_api),

    url(r'^submissions/$',
        submissions,
        {"format": "json"},
        name='submissions'),

    url(r'^submissions/(?P<format>[a-z]+)/$',
        submissions,
        name='submissions_by_format'),

    url(r'^submission/(?P<id>\d+)/$',
        submission),

    url(r'^submission_for_map/(?P<id>\d+)/$',
        submission_for_map),

    url(r'^location_question_results/(?P<question_id>\d+)/(?P<limit_map_answers>\d+)/$',
        location_question_results,
        kwargs={"survey_report_slug": ""}),

    url(r'^location_question_results/(?P<question_id>\d+)/(?P<limit_map_answers>\d*)/(?P<survey_report_slug>[-a-z0-9_]*)/$',
        location_question_results,
        name="location_question_results"),

    url(r'^location_question_map/(?P<question_id>\d+)/(?P<display_id>\d+)/$',
        location_question_map,
        name="location_question_map"),

    url(r'^location_question_map/(?P<question_id>\d+)/(?P<display_id>\d+)/(?P<survey_report_slug>[-a-z0-9_]*)/$',
        location_question_map,
        name="location_question_map"),

    url(r'^s/(?P<slug>[-a-z0-9_]+)/$',
        survey_detail,
        name="survey_detail"),

    url(r'^s/(?P<slug>[-a-z0-9_]+)/done$',
        survey_completed,
        name="survey_completed"),

    url(r'^s/(?P<slug>[-a-z0-9_]+)/api/allowed_actions/$',
        allowed_actions,
        name="allowed_actions"),

    url(r'^s/(?P<slug>[-a-z0-9_]+)/api/questions/$',
        questions,
        name="questions"),

    url(r'^s/(?P<slug>[-a-z0-9_]+)/api/embeded_survey_questions/$',
        embeded_survey_questions,
        name="embeded_survey_questions"),

    # Survey reports
    # --------------

    url(r'^r/(?P<slug>[-a-z0-9_]+)/api/$',
        embeded_survey_report,
        {"report": ""},
        name="embeded_survey_report_default"),

    url(r'^r/(?P<slug>[-a-z0-9_]+)/api/$',
        embeded_survey_report,
        name="embeded_survey_report"),

    url(r'^s/(?P<slug>[-a-z0-9_]+)/report/$',
        SurveyReportView.as_view(),
        name="survey_default_report_page_1"),

    url(r'^s/(?P<slug>[-a-z0-9_]+)/report/(?P<page>\d+)/$',
        SurveyReportView.as_view(),
        name="survey_default_report"),

    url(r'^r/(?P<slug>[-a-z0-9_]+)$',
        SurveyReportView.as_view(),
        name="survey_report_page_1"),

    url(r'^r/(?P<slug>[-a-z0-9_]+)/(?P<page>\d+)/$',
        SurveyReportView.as_view(),
        name="survey_report")
    )
