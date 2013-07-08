from __future__ import absolute_import

import csv
from datetime import datetime
import httplib
from itertools import count
import logging
import smtplib
from xml.dom.minidom import Document
#from Tools.Scripts.classfix import rep
import django.views.generic

from django.conf import settings
from django.contrib.sites.models import Site
from django.contrib.auth.models import User
from django.core.exceptions import FieldError
from django.core.mail import EmailMultiAlternatives
from django.core.paginator import Paginator, EmptyPage, InvalidPage
from django.core.urlresolvers import reverse, NoReverseMatch
from django.db.models import Q
from django.http import HttpResponse, HttpResponseRedirect, Http404
from django.shortcuts import get_object_or_404, render_to_response
from django.template import RequestContext as _rc
from django.template.loader import render_to_string
from django.utils.html import escape

from .forms import forms_for_survey
from .models import (
    Answer,
    BALLOT_STUFFING_FIELDS,
    FORMAT_CHOICES,
    OPTION_TYPE_CHOICES,
    Question,
    SURVEY_DISPLAY_TYPE_CHOICES,
    Submission,
    Survey,
    SurveyReport,
    SurveyReportDisplay,
    extra_from_filters,
    get_all_answers,
    #get_filters
    )
from .jsonutils import dump, dumps, datetime_to_string

from .util import ChoiceEnum, get_function, get_session, get_user
from . import settings as crowdsourcing_settings


def allow_origin_sites():
    database_sites = []
    sites = Site.objects.all()
    for protocol in ["http", "https"]:
        database_sites += ["%s://%s" % (protocol, s.domain) for s in sites]
    config_sites = getattr(settings, "ADDITIONAL_CORS_SITES", [])
    return list(set(database_sites + config_sites))


def api_response(request, data, callback=None, format='json'):
    # http://www.loggly.com/blog/2011/12/enabling-cors-in-django-piston/
    # for how to enable CORS
    request_method = request.method.upper()
    if request_method in ["OPTIONS", "HEAD"]:
        response = HttpResponse()
    elif isinstance(data, HttpResponseRedirect):
        response = data
    elif callback:
        body = u'%s(%s);' % (callback, dumps(data))
        response = HttpResponse(body, mimetype='application/javascript')
    elif format == 'html':
        response = HttpResponse(data)
    else:
        response = HttpResponse(mimetype='application/json')
        dump(data, response)

    origin = request.META.get('HTTP_REFERER', '') or \
             request.META.get('HTTP_ORIGIN', '')
    allowed = allow_origin_sites()
    if origin:
        allowed = [a for a in allowed if origin.find(a) >= 0]
    response["Access-Control-Allow-Origin"] = " ".join(allowed)
    response['Access-Control-Allow-Methods'] = \
        'POST, GET, OPTIONS, HEAD, PUT, DELETE'
    response["Access-Control-Allow-Headers"] = 'Authorization'
    response["Access-Control-Allow-Credentials"] = 'true'

    return response


def api_response_decorator(format='json'):
    def _api_response_decorator(the_func):
        def _decorated(request, *args, **kwargs):
            request_method = request.method.upper()
            if request_method == "OPTIONS":
                return api_response(request, {}, format=format)
            result = the_func(request, *args, **kwargs)
            if isinstance(result, HttpResponse):
                return result
            callback = request.GET.get('callback', None)
            return api_response(
                    request, result, format=format, callback=callback)
        return _decorated
    return _api_response_decorator


def _user_entered_survey(request, survey):
    if not get_user(request).is_authenticated():
        return False
    return bool(survey.submissions_for(
        get_user(request),
        get_session(request).session_key.lower()).count())


def _entered_no_more_allowed(request, survey):
    """ The user entered the survey and the survey allows only one entry. """
    return all((
        not survey.allow_multiple_submissions,
        _user_entered_survey(request, survey),))


def _get_remote_ip(request):
    forwarded=request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[-1].strip()
    return request.META['REMOTE_ADDR']


def _login_url(request):
    if crowdsourcing_settings.LOGIN_VIEW:
        start_with = reverse(crowdsourcing_settings.LOGIN_VIEW) + '?next=%s'
        return start_with % request.path
    return "/?login_required=true"


def _get_survey_or_404(slug, request=None):
    manager = Survey.live
    if request and get_user(request).is_staff:
        manager = Survey.objects
    return get_object_or_404(manager, slug=slug)


def _survey_submit(request, survey):
    if survey.require_login and get_user(request).is_anonymous():
        # again, the form should only be shown after the user is logged in, but
        # to be safe...
        return HttpResponseRedirect(_login_url(request))
    if not hasattr(request, 'session'):
        return HttpResponse("Cookies must be enabled to use this application.",
                            status=httplib.FORBIDDEN)
    if (_entered_no_more_allowed(request, survey)):
        slug_template = 'crowdsourcing/%s_already_submitted.html' % survey.slug
        return render_to_response([slug_template,
                                   'crowdsourcing/already_submitted.html'],
                                  dict(survey=survey),
                                  _rc(request))

    forms = forms_for_survey(survey, request)

    if _submit_valid_forms(forms, request, survey):
        if survey.can_have_public_submissions():
            return _survey_results_redirect(request, survey, thanks=True)
        return _survey_show_form(request, survey, ())
    else:
        return _survey_show_form(request, survey, forms)


def _submit_valid_forms(forms, request, survey):
    if not all(form.is_valid() for form in forms):
        return False
    submission_form = forms[0]
    submission = submission_form.save(commit=False)
    submission.survey = survey
    submission.ip_address = _get_remote_ip(request)
    submission.is_public = not survey.moderate_submissions
    if get_user(request).is_authenticated():
        submission.user = get_user(request)
    submission.save()
    for form in forms[1:]:
        answer = form.save(commit=False)
        if isinstance(answer, (list, tuple)):
            for a in answer:
                a.submission = submission
                a.save()
        elif answer:
                answer.submission = submission
                answer.save()
    if survey.email:
        _send_survey_email(request, survey, submission)
    return True


def _url_for_edit(request, obj):
    view_args = (obj._meta.app_label, obj._meta.module_name,)
    try:
        edit_url = reverse("admin:%s_%s_change" % view_args, args=(obj.id,))
    except NoReverseMatch:
        # Probably 'admin' is not a registered namespace on a site without an
        # admin. Just fake it.
        edit_url = "/admin/%s/%s/%d/" % (view_args + (obj.id,))
    admin_url = crowdsourcing_settings.SURVEY_ADMIN_SITE
    if not admin_url:
        admin_url = "http://" + request.META["HTTP_HOST"]
    elif len(admin_url) < 4 or admin_url[:4].lower() != "http":
        admin_url = "http://" + admin_url
    return admin_url + edit_url


def _send_survey_email(request, survey, submission):
    recipients = [a.strip() for a in survey.email.split(",")]
    if crowdsourcing_settings.ALL_STAFF_EMAIL_NOTIFICATION:
        staff = recipients
        public = []
    else:
        staff_users = User.objects.filter(email__in=recipients, is_staff=True)
        staff = list(set([u.email for u in staff_users]))
        public = [e for e in recipients if not e in staff]

    def _send_msg(subject, parts, emails):
        html_email = "<br/>\n".join(parts)
        sender = crowdsourcing_settings.SURVEY_EMAIL_FROM
        email_msg = EmailMultiAlternatives(subject, html_email, sender, emails)
        email_msg.attach_alternative(html_email, 'text/html')
        try:
            email_msg.send()
        except smtplib.SMTPException as ex:
            logging.exception("SMTP error sending email: %s" % str(ex))
        except Exception as ex:
            logging.exception("Unexpected error sending email: %s" % str(ex))

    answs = Answer.objects.filter(submission=submission)
    host = "http://" + request.META["HTTP_HOST"]
    report_url = host + _survey_report_url(survey)
    if staff:
        links = [(_url_for_edit(request, submission), "Edit Submission"),
                 (_url_for_edit(request, survey), "Edit Survey"),]
        if survey.can_have_public_submissions():
            u = report_url
            links.append((u, "View Survey",))
        parts = ["<a href=\"%s\">%s</a>" % link for link in links]
        lines = ["%s: %s" % (a.question.label, escape(a.value),) for a in answs]
        parts.extend(lines)
        _send_msg(survey.title, parts, staff)
    if public:
        subject = []
        body = []
        OTC = OPTION_TYPE_CHOICES
        for ans in answs:
            if ans.value:
                opt_type = ans.question.option_type
                if any([opt_type in (OTC.SELECT, OTC.LOCATION),
                        ans.question.question.lower().find("title") >= 0]):
                    subject.append(ans.value)
                elif opt_type in (OTC.PHOTO):
                    image_src = host + ans.image_answer.thumbnail.absolute_url
                    body.append("<img src='%s' />" % (image_src))
                else:
                    body.append(ans.value)
        body.append("<a href='%s'>See the survey</a>" % report_url)
        subject = ". ".join(subject) or survey.title
        html_email = "<br/>\n".join(body)
        _send_msg(subject, body, public)


def _survey_show_form(request, survey, forms):
    specific_template = 'crowdsourcing/%s_survey_detail.html' % survey.slug
    entered = _user_entered_survey(request, survey)
    return render_to_response([specific_template,
                               'crowdsourcing/survey_detail.html'],
                              dict(survey=survey,
                                   forms=forms,
                                   entered=entered,
                                   login_url=_login_url(request),
                                   request=request),
                              _rc(request))


def _can_show_form(request, survey):
    authenticated = get_user(request).is_authenticated()
    return all((
        survey.is_open,
        authenticated or not survey.require_login,
        not _entered_no_more_allowed(request, survey)))


def survey_detail(request, slug):
    """ When you load the survey, this view decides what to do. It displays
    the form, redirects to the results page, displays messages, or whatever
    makes sense based on the survey, the user, and the user's entries. """
    survey = _get_survey_or_404(slug, request)
    if not survey.is_open and survey.can_have_public_submissions():
        return _survey_results_redirect(request, survey)
    need_login = (survey.is_open
                  and survey.require_login
                  and not get_user(request).is_authenticated())
    if _can_show_form(request, survey):
        if request.method == 'POST':
            return _survey_submit(request, survey)
        forms = forms_for_survey(survey, request)
    elif need_login:
        forms = ()
        if crowdsourcing_settings.LOGIN_AUTOREDIRECT:
            return HttpResponseRedirect(_login_url(request))
    elif survey.can_have_public_submissions():
        return _survey_results_redirect(request, survey)
    else: # Survey is closed with private results.
        forms = ()
    return _survey_show_form(request, survey, forms)


@api_response_decorator(format='html')
def embeded_survey_questions(request, slug):
    survey = _get_survey_or_404(slug, request)
    templates = ['crowdsourcing/embeded_survey_questions_%s.html' % slug,
                 'crowdsourcing/embeded_survey_questions.html']
    forms = ()
    if _can_show_form(request, survey):
        forms = forms_for_survey(survey, request)
        if request.method == 'POST':
            if _submit_valid_forms(forms, request, survey):
                forms = ()
    return render_to_string(templates, dict(
        entered=_user_entered_survey(request, survey),
        request=request,
        forms=forms,
        survey=survey,
        login_url=_login_url(request)), _rc(request))


def _survey_results_redirect(request, survey, thanks=False):
    response = HttpResponseRedirect(_survey_report_url(survey))
    if thanks:
        get_session(request)['survey_thanks_%s' % survey.slug] = '1'
    return response


def _survey_report_url(survey):
    return reverse('survey_default_report_page_1',
                   kwargs={'slug': survey.slug})


@api_response_decorator()
def allowed_actions(request, slug):
    survey = _get_survey_or_404(slug, request)
    authenticated = get_user(request).is_authenticated()
    return {
        "enter": _can_show_form(request, survey),
        "view": survey.can_have_public_submissions(),
        "open": survey.is_open,
        "need_login": survey.require_login and not authenticated}


@api_response_decorator()
def questions(request, slug):
    return _get_survey_or_404(slug, request).to_jsondata()


def submissions(request, format):
    """ Use this view to make arbitrary queries on submissions. If the user is
    a logged in staff member, ignore submission.is_public,
    question.answer_is_public, and survey.can_have_public_submissions. Use the
    query string to pass keys and values. For example,
    /crowdsourcing/submissions/?survey=my-survey will return all submissions
    for the survey with slug my-survey.
    survey - the slug for the survey
    user - the username of the submittor. Leave blank for submissions without
        a logged in user.
    submitted_from and submitted_to - strings in the format YYYY-mm-ddThh:mm:ss
        For example, 2010-04-05T13:02:03
    featured - A blank value, 'f', 'false', 0, 'n', and 'no' all mean ignore
        the featured flag. Everything else means display only featured.
    You can also use filters in the survey report sense. Rather than document
    exactly what parameters you would pass, follow these steps to figure it
    out:
    1. Enable filters on your survey and the questions you want to filter on.
    2. Go to the report page and fill out the filters you want.
    3. Click Submit.
    4. Examine the query string of the page you end up on and note which
        parameters are filled out. Use those same parameters here. """
    format = format.lower()
    if format not in FORMAT_CHOICES:
        msg = ("%s is an unrecognized format. Crowdsourcing recognizes "
               "these: %s") % (format, ", ".join(FORMAT_CHOICES))
        return HttpResponse(msg)
    is_staff = get_user(request).is_authenticated() and get_user(request).is_staff
    if is_staff:
        results = Submission.objects.all()
    else:
        # survey.can_have_public_submissions is complicated enough that
        # we'll check it in Python, not the database.
        results = Submission.objects.filter(is_public=True)
    results = results.select_related("survey", "user")
    get = request.GET.copy()
    limit = int(get.pop("limit", [0])[0])
    keys = get.keys()
    basic_filters = (
        'survey',
        'user',
        'submitted_from',
        'submitted_to',
        'featured',
        'is_public')
    if is_staff:
        basic_filters += BALLOT_STUFFING_FIELDS
    survey_slug = ""
    for field in [f for f in keys if f in basic_filters]:
        value = get[field]
        search_field = field
        if 'survey' == field:
            search_field = 'survey__slug'
            survey_slug = value
        elif 'user' == field:
            if '' == value:
                value = None
            else:
                search_field = 'user__username'
        elif field in ('submitted_from', 'submitted_to'):
            date_format = "%Y-%m-%dT%H:%M:%S"
            try:
                value = datetime.strptime(value, date_format)
            except ValueError:
                return HttpResponse(
                    ("Invalid %s format. Try, for example, "
                     "%s") % (field, datetime.now().strftime(date_format),))
            if 'submitted_from' == field:
                search_field = 'submitted_at__gte'
            else:
                search_field = 'submitted_at__lte'
        elif field in('featured', 'is_public',):
            falses = ('f', 'false', 'no', 'n', '0',)
            value = len(value) and not value.lower() in falses
        # search_field is unicode but needs to be ascii.
        results = results.filter(**{str(search_field): value})
        get.pop(field)

    def get_survey():
        survey = Survey.objects.get(slug=survey_slug)
        get_survey = lambda: survey
        return survey

    if get:
        if survey_slug:
            results = extra_from_filters(
                results,
                "crowdsourcing_submission.id",
                get_survey(),
                get)
        else:
            message = (
                "You've got a couple of extra filters here, and we "
                "aren't sure what to do with them. You may have just "
                "misspelled one of the basic filters (%s). You may have a "
                "filter from a particular survey in mind. In that case, just "
                "include survey=my-survey-slug in the query string. You may "
                "also be trying to pull some hotshot move like, \"Get me all "
                "submissions that belong to a survey with a filter named '%s' "
                "that match '%s'.\" Crowdsourcing could support this, but it "
                "would be pretty inefficient and, we're guessing, pretty "
                "rare. If that's what you're trying to do I'm afraid you'll "
                "have to do something more complicated like iterating through "
                "all your surveys.")
            item = get.items()[0]
            message = message % (", ".join(basic_filters), item[0], item[1])
            return HttpResponse(message)
    if not is_staff:
        if survey_slug:
            if not get_survey().can_have_public_submissions():
                results = []
        else:
            rs = [r for r in results if r.survey.can_have_public_submissions()]
            results = rs
    if limit:
        results = results[:limit]
    answer_lookup = get_all_answers(results,
                                    include_private_questions=is_staff)
    result_data = []
    for r in results:
        data = r.to_jsondata(answer_lookup, include_private_questions=is_staff)
        result_data.append(data)

    for data in result_data:
        data.update(data["data"])
        data.pop("data")

    def get_keys():
        key_lookup = {}
        for data in result_data:
            for key in data.keys():
                key_lookup[key] = True
        return sorted(key_lookup.keys())

    if format == 'json':
        response = HttpResponse(mimetype='application/json')
        dump(result_data, response)
    elif format == 'csv':
        response = HttpResponse(mimetype='text/csv')
        writer = csv.writer(response)
        keys = get_keys()
        writer.writerow(keys)
        for data in result_data:
            row = []
            for k in keys:
                row.append((u"%s" % _encode(data.get(k, ""))).encode("utf-8"))
            writer.writerow(row)
    elif format == 'xml':
        doc = Document()
        submissions = doc.createElement("submissions")
        doc.appendChild(submissions)
        for data in result_data:
            submission = doc.createElement("submission")
            submissions.appendChild(submission)
            for key, value in data.items():
                if value:
                    cell = doc.createElement(key)
                    submission.appendChild(cell)
                    cell.appendChild(doc.createTextNode(u"%s" % value))
        response = HttpResponse(doc.toxml(), mimetype='text/xml')
    elif format == 'html': # mostly for debugging.
        keys = get_keys()
        results = [
            "<html><body><table>",
            "<tr>%s</tr>" % "".join(["<th>%s</th>" % k for k in keys])]
        for data in result_data:
            cell = "<td>%s</td>"
            cells = [cell % _encode(data.get(key, "")) for key in keys]
            results.append("<tr>%s</tr>" % "".join(cells))
        results.append("</table></body></html>")
        response = HttpResponse("\n".join(results))
    else:
        return HttpResponse("Unsure how to handle %s format" % format)
    return response


def _encode(possible):
    if possible is True:
        return 1
    elif possible is False:
        return 0
    return datetime_to_string(possible) or possible


def submission(request, id):
    template = 'crowdsourcing/submission.html'
    sub = get_object_or_404(Submission.objects, is_public=True, pk=id)
    return render_to_response(template, dict(submission=sub), _rc(request))


def _default_report(survey, is_staff):
    field_count = count(1)
    OTC = OPTION_TYPE_CHOICES
    pie_choices = (
        OTC.BOOL,
        OTC.SELECT,
        OTC.CHOICE,
        OTC.NUMERIC_SELECT,
        OTC.NUMERIC_CHOICE,
        OTC.BOOL_LIST,)
    all_choices = pie_choices + (OTC.LOCATION, OTC.PHOTO)
    public_fields = survey.get_fields() if is_staff else survey.get_public_fields()
    fields = [f for f in public_fields if f.option_type in all_choices]
    report = SurveyReport(
        survey=survey,
        title=survey.title,
        summary=survey.description or survey.tease)
    displays = []
    for field in fields:
        if field.option_type in pie_choices:
            type = SURVEY_DISPLAY_TYPE_CHOICES.PIE
        elif field.option_type == OTC.LOCATION:
            type = SURVEY_DISPLAY_TYPE_CHOICES.MAP
        elif field.option_type == OTC.PHOTO:
            type = SURVEY_DISPLAY_TYPE_CHOICES.SLIDESHOW
        displays.append(SurveyReportDisplay(
            report=report,
            display_type=type,
            fieldnames=field.fieldname,
            annotation=field.label,
            order=field_count.next()))
    report.survey_report_displays = displays
    return report


class SurveyReportView(django.views.generic.TemplateView):
    """
    Show report for a survey(s)
    """

    def _get_report(self):
        """
        Override this for other views that discover report in other way
        """
        return get_object_or_404(SurveyReport, slug=self.kwargs['slug'])

    def get_template_names(self):
        slug = self._get_report().slug
        return ['crowdsourcing/survey_report_%s.html' % slug,
                 'crowdsourcing/survey_report.html']

    def _get_surveys(self, report):
        """
        Get a map of the survey(s) for this report.  The returned map will be
        a map from survey slug->survey object.

        @type report SurveyReport
        """
        survey_pairs = [(survey.slug, survey) for survey in report.survey.all()]
        return dict(survey_pairs)

    def get_context_data(self, **kwargs):
        user_is_staff = self.request.user.is_staff
        report_obj = self._get_report()
        page = self.kwargs.get('page', 1)
        try:
            page = int(page)
        except ValueError:
            raise Http404

        context = dict()
        context['surveys'] = surveys = self._get_surveys(report_obj)
        context['is_public'] = all([survey.is_live and survey.can_have_public_submissions() for survey in surveys.values()])
        if not context['is_public'] and not user_is_staff:
            raise Http404
        context['archive_fields'] = self._get_archive_fields(surveys)
        context['filters'] = self._get_filters(surveys)
        if not report_obj.display_individual_results:
            context['submissions'] = Submission.objects.none()
        else:
            context['submissions'] = self._get_submissions(surveys, not user_is_staff, report_obj)
        context['fields'] = self._get_fields(surveys,not user_is_staff)
        all_submissions = reduce(lambda a,b:a+b, context['submissions'].values())
        paginator, page_obj = paginate_or_404(all_submissions, page)
        context['paginator'] = paginator
        context['page_obj'] = page_obj

        context['page_answers'] = get_all_answers(
            page_obj.object_list,
            include_private_questions=user_is_staff)

        context['pages_to_link'] = pages_to_link_from_paginator(page, paginator)

        context['display_individual_results'] = all([
            report_obj.display_individual_results,
            context['archive_fields'] or (user_is_staff and context['fields'])])

        return context


    def _get_archive_fields(self, surveys):
        """
        Get map of archive fields.  survey slug=>archive fields list
        @type surveys dict(String, Survey)
        """
        pairs = [(survey.slug, list(survey.get_public_archive_fields())) for survey in surveys.values()]
        return dict(pairs)

    def _get_survey_submissions(self, survey, public_only, report_obj):
        if public_only:
            submissions = survey.public_submissions()
        else:
            submissions = survey.submission_set.all()
        id_field = "crowdsourcing_submission.id"
        submissions = extra_from_filters(submissions,
                                         id_field,
                                         survey,
                                         self.request.GET)
        # If you want to sort based on rating, wire it up here.
        if crowdsourcing_settings.PRE_REPORT:
            pre_report = get_function(crowdsourcing_settings.PRE_REPORT)
            submissions = pre_report(
                submissions=submissions,
                report=report_obj,
                request=self.request)
        if report_obj.featured:
            submissions = submissions.filter(featured=True)
        if report_obj.limit_results_to:
            submissions = submissions[:report_obj.limit_results_to]

        return submissions

    def _get_submissions(self, surveys, public_only, report_obj):
        pairs = [(survey.slug, self._get_survey_submissions(survey, public_only, report_obj)) for survey in surveys.values()]
        return dict(pairs)

    def _get_fields(self, surveys, public_only=True):
        if public_only:
            pairs = [(survey.slug, list(survey.get_public_fields())) for survey in surveys.values()]
        else:
            pairs = [(survey.slug, list(survey.get_fields())) for survey in surveys.values()]
        return dict(pairs)

    def _get_filters(self, surveys):
        pairs = [(survey.slug, survey.get_filters(self.request.GET)) for survey in surveys.values()]
        return dict(pairs)




@api_response_decorator(format='html')
def embeded_survey_report(request, slug, report=''):
    templates = ['crowdsourcing/embeded_survey_report_%s.html' % slug,
                 'crowdsourcing/embeded_survey_report.html']
    return _survey_report(request, slug, report, None, templates)


def _survey_report(request, slug, report, page, templates):
    """ Show a report for the survey. As rating is done in a separate
    application we don't directly check request.GET["sort"] here.
    crowdsourcing_settings.PRE_REPORT is the place for that. """
    is_staff = get_user(request).is_staff
    if page is None:
        page = 1
    else:
        try:
            page = int(page)
        except ValueError:
            raise Http404
    survey = _get_survey_or_404(slug, request)
    # is the survey anything we can actually have a report on?
    is_public = survey.is_live and survey.can_have_public_submissions()
    if not is_public and not is_staff:
        raise Http404
    reports = survey.surveyreport_set.all()
    if report:
        report_obj = get_object_or_404(reports, slug=report)
    elif survey.default_report:
        args = {"slug": survey.slug, "report": survey.default_report.slug}
        url = reverse("survey_report_page_1", kwargs=args)
        return HttpResponseRedirect(url)
    else:
        report_obj = _default_report(survey, is_staff)

    if is_staff:
        archive_fields = list(survey.get_archive_fields())
        submissions = survey.submission_set.all()
        fields = list(survey.get_fields())
    else:
        archive_fields = list(survey.get_public_archive_fields())
        submissions = survey.public_submissions()
        fields = list(survey.get_public_fields())
    filters = survey.get_filters(request.GET)

    id_field = "crowdsourcing_submission.id"
    if not report_obj.display_individual_results:
        submissions = submissions.none()
    else:
        submissions = extra_from_filters(submissions,
                                         id_field,
                                         survey,
                                         request.GET)
        # If you want to sort based on rating, wire it up here.
        if crowdsourcing_settings.PRE_REPORT:
            pre_report = get_function(crowdsourcing_settings.PRE_REPORT)
            submissions = pre_report(
                submissions=submissions,
                report=report_obj,
                request=request)
        if report_obj.featured:
            submissions = submissions.filter(featured=True)
        if report_obj.limit_results_to:
            submissions = submissions[:report_obj.limit_results_to]
    paginator, page_obj = paginate_or_404(submissions, page)

    page_answers = get_all_answers(
        page_obj.object_list,
        include_private_questions=is_staff)

    pages_to_link = pages_to_link_from_paginator(page, paginator)

    display_individual_results = all([
        report_obj.display_individual_results,
        archive_fields or (is_staff and fields)])
    context = dict(
        survey=survey,
        submissions=submissions,
        paginator=paginator,
        page_obj=page_obj,
        pages_to_link=pages_to_link,
        fields=fields,
        archive_fields=archive_fields,
        filters=filters,
        report=report_obj,
        page_answers=page_answers,
        is_public=is_public,
        display_individual_results=display_individual_results,
        request=request,
        is_staff=get_user(request).is_staff)

    return render_to_string(templates, context, _rc(request))


def pages_to_link_from_paginator(page, paginator):
    """ Return an array with numbers where you should link to a page, and False
    where you should show elipses. For example, if you have 9 pages and you are
    on page 9, return [1, False, 5, 6, 7, 8, 9]. """
    pages = []
    for i in range(page - 4, page + 5):
        if 1 <= i <= paginator.num_pages:
            pages.append(i)
    if pages[0] > 1:
        pages = [1, False] + pages
    if pages[-1] < paginator.num_pages:
        pages = pages + [False, paginator.num_pages]

    DISCARD = -999
    for i in range(1, len(pages) - 1):
        if pages[i - 1] + 2 == pages[i + 1]:
            # Turn [1, False, 3... into [1, 2, 3
            pages[i] = (pages[i - 1] + pages[i + 1]) / 2
        elif pages[i - 1] + 1 == pages[i + 1]:
            # Turn [1, False, 2... into [1, DISCARD, 2...
            pages[i] = DISCARD

    return [p for p in pages if p != DISCARD]


def paginate_or_404(queryset, page, num_per_page=20):
    """
    paginate a queryset (or other iterator) for the given page, returning the
    paginator and page object. Raises a 404 for an invalid page.
    """
    if page is None:
        page = 1
    paginator = Paginator(queryset, num_per_page)
    try:
        page_obj = paginator.page(page)
    except EmptyPage, InvalidPage:
        raise Http404
    return paginator, page_obj


def location_question_results(
    request,
    question_id,
    limit_map_answers,
    survey_report_slug=""):
    question = get_object_or_404(Question.objects.select_related("survey"),
                                 pk=question_id,
                                 answer_is_public=True)
    is_staff = get_user(request).is_staff
    if not question.survey.can_have_public_submissions() and not is_staff:
        raise Http404
    featured = limit_results_to = False
    if survey_report_slug:
        survey_report = get_object_or_404(SurveyReport.objects,
                                          survey=question.survey,
                                          slug=survey_report_slug)
        featured = survey_report.featured
        limit_results_to = survey_report.limit_results_to
    icon_lookup = {}
    icon_questions = question.survey.icon_questions()
    for icon_question in icon_questions:
        icon_by_answer = {}
        for (option, icon) in icon_question.parsed_option_icon_pairs():
            if icon:
                icon_by_answer[option] = icon
        answer_set = icon_question.answer_set.all()
        for answer in answer_set.select_related("question"):
            if answer.value in icon_by_answer:
                icon = icon_by_answer[answer.value]
                icon_lookup[answer.submission_id] = icon

    answers = question.answer_set.filter(
        ~Q(latitude=None),
        ~Q(longitude=None)).order_by("-submission__submitted_at")
    if not is_staff:
        answers = answers.filter(submission__is_public=True)
    if featured:
        answers = answers.filter(submission__featured=True)
    answers = extra_from_filters(
        answers,
        "submission_id",
        question.survey,
        request.GET)
    limit_map_answers = int(limit_map_answers) if limit_map_answers else 0
    if limit_map_answers or limit_results_to:
        answers = answers[:min(filter(None, [limit_map_answers,
                                             limit_results_to,]))]
    entries = []
    view = "crowdsourcing.views.submission_for_map"
    for answer in answers:
        kwargs = {"id": answer.submission_id}
        d = {
            "lat": answer.latitude,
            "lng": answer.longitude,
            "url": reverse(view, kwargs=kwargs)}
        if answer.submission_id in icon_lookup:
            d["icon"] = icon_lookup[answer.submission_id]
        entries.append(d)
    response = HttpResponse(mimetype='application/json')
    dump({"entries": entries}, response)
    return response

def location_question_map(
    request,
    question_id,
    display_id,
    survey_report_slug=""):

    question = Question.objects.get(pk=question_id)
    if not question.answer_is_public and not get_user(request).is_staff:
        raise Http404
    report = None
    limit = 0

    if survey_report_slug:
        report = SurveyReport.objects.get(slug=survey_report_slug,
                                          survey=question.survey)
        limit = report.limit_results_to
    else:
        report = _default_report(question.survey, get_user(request).is_staff)

    # This cast is not for validation since the urls file already guaranteed
    # it would be a nonempty string of digits. It's simply because display_id
    # is a string.
    if int(display_id):
        display = SurveyReportDisplay.objects.get(pk=display_id)
    else:
        for d in report.survey_report_displays:
            if question.pk in [q.pk for q in d.questions()]:
                display = d
                display.limit_map_answers = limit

    return render_to_response('crowdsourcing/location_question_map.html', dict(
        display=display,
        question=question,
        report=report))

def submission_for_map(request, id):
    template = 'crowdsourcing/submission_for_map.html'
    if get_user(request).is_staff:
        sub = get_object_or_404(Submission.objects, pk=id)
    else:
        sub = get_object_or_404(Submission.objects, is_public=True, pk=id)
    return render_to_response(template, dict(submission=sub), _rc(request))
