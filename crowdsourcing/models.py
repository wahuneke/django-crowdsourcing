from __future__ import absolute_import

import datetime
import logging
from operator import itemgetter

from django.contrib.auth.models import User
from django.db import models
from django.db.models.fields.files import ImageFieldFile
from django.utils.translation import ugettext_lazy as _

from .fields import ImageWithThumbnailsField
from .geo import get_latitude_and_longitude
from .util import ChoiceEnum
from . import settings as local_settings

try:
    from .flickrsupport import sync_to_flickr
except ImportError:
    logging.warn('no flickr support available')
    
    sync_to_flickr=None



ARCHIVE_POLICY_CHOICES=ChoiceEnum(('immediate',
                                   'post-close',
                                   'never'))


class LiveSurveyManager(models.Manager):
    def get_query_set(self):
        now=datetime.datetime.now()
        return super(LiveSurveyManager, self).get_query_set().filter(
            is_published=True,
            starts_at__lte=now).filter(
            ~models.Q(archive_policy__exact=ARCHIVE_POLICY_CHOICES.NEVER) | 
            models.Q(ends_at__isnull=True) |
            models.Q(ends_at__gt=now))


class Survey(models.Model):
    title=models.CharField(max_length=80)
    slug=models.SlugField(unique=True)
    tease=models.TextField(blank=True)
    description=models.TextField(blank=True)
    
    require_login=models.BooleanField(default=False)
    allow_multiple_submissions=models.BooleanField(default=False)
    moderate_submissions=models.BooleanField(default=local_settings.MODERATE_SUBMISSIONS)
    archive_policy=models.results=models.IntegerField(choices=ARCHIVE_POLICY_CHOICES,
                                                      default=ARCHIVE_POLICY_CHOICES.IMMEDIATE)

    starts_at=models.DateTimeField(default=datetime.datetime.now)
    survey_date=models.DateField(blank=True, null=True, editable=False)
    ends_at=models.DateTimeField(null=True, blank=True)
    is_published=models.BooleanField(default=False)

    # Flickr integration
    flickr_set_id=models.CharField(max_length=60, blank=True)

    def to_jsondata(self):
        return dict(title=self.title,
                    slug=self.slug,
                    description=self.description,
                    questions=[q.to_jsondata() for q in self.questions.filter(answer_is_public=True)])
    

    def save(self, **kwargs):
        self.survey_date=self.starts_at.date()
        super(Survey, self).save(**kwargs)

    class Meta:
        ordering=('-starts_at',)
        unique_together=(('survey_date', 'slug'),)

    @property
    def is_open(self):
        now=datetime.datetime.now()
        if self.ends_at:
            return self.starts_at <= now < self.ends_at
        else:
            return self.starts_at <= now


    def get_public_location_fields(self):
        return self.questions.filter(option_type==OPTION_TYPE_CHOICES.LOCATION_FIELD,
                                     answer_is_public=True)

    def get_public_archive_fields(self):
        return self.questions.filter(option_type__in=(OPTION_TYPE_CHOICES.TEXT_FIELD,
                                                      OPTION_TYPE_CHOICES.PHOTO_UPLOAD,
                                                      OPTION_TYPE_CHOICES.VIDEO_LINK,
                                                      OPTION_TYPE_CHOICES.TEXT_AREA),
                                     answer_is_public=True)

    def get_public_aggregate_fields(self):
        return self.questions.filter(option_type__in=(OPTION_TYPE_CHOICES.INTEGER,
                                                      OPTION_TYPE_CHOICES.FLOAT,
                                                      OPTION_TYPE_CHOICES.BOOLEAN,
                                                      OPTION_TYPE_CHOICES.SELECT_ONE_CHOICE,
                                                      OPTION_TYPE_CHOICES.RADIO_LIST,
                                                      OPTION_TYPE_CHOICES.CHECKBOX_LIST),
                                     answer_is_public=True)
        

    def submissions_for(self, user, session_key):
        q=models.Q(survey=self)
        if user.is_authenticated():
            q=q & models.Q(user=user)
        elif session_key:
            q=q & models.Q(session_key=session_key)
        else:
            # can't pinpoint user, return none
            return Submission.objects.none()
        return Submission.objects.filter(q)

    def public_submissions(self):
        if self.archive_policy==ARCHIVE_POLICY_CHOICES.NEVER or (
            self.archive_policy==ARCHIVE_POLICY_CHOICES.POST_CLOSE and
            self.is_open):
            return self.submission_set.none()
        return self.submission_set.filter(is_public=True)

    def __unicode__(self):
        return self.title

    @models.permalink
    def get_absolute_url(self):
        return ('survey_detail', (), {'slug': self.slug })

    objects=models.Manager()
    live=LiveSurveyManager()

    
OPTION_TYPE_CHOICES = ChoiceEnum(sorted([('char', 'Text Field'),
                                         ('email', 'Email Field'),
                                         ('photo', 'Photo Upload'),
                                         ('video', 'Video Link'),
                                         ('location', 'Location Field'),
                                         ('integer', 'Integer'),
                                         ('float', 'Float'),
                                         ('bool', 'Boolean'),
                                         ('text', 'Text Area'),
                                         ('select', 'Select One Choice'),
                                         ('radio', 'Radio List'),
                                         ('checkbox', 'Checkbox List'),],
                                        key=itemgetter(1)))

                                 
class Question(models.Model):
    survey=models.ForeignKey(Survey, related_name="questions")
    fieldname=models.CharField('field name',
                               max_length=32,
                               help_text=_('a single-word identifier used to track this value; '
                                           'it must begin with a letter and may contain alphanumerics and underscores (no spaces).'))
    question=models.TextField()
    help_text=models.TextField(blank=True)
    required=models.BooleanField(default=False)
    order=models.IntegerField(null=True, blank=True)
    option_type=models.CharField(max_length=12, choices=OPTION_TYPE_CHOICES)
    options=models.TextField(blank=True, default='')
    answer_is_public=models.BooleanField(default=True)

    def to_jsondata(self):
        return dict(fieldname=self.fieldname,
                    question=self.question,
                    required=self.required,
                    option_type=self.option_type,
                    options=self.parsed_options)
                    

    class Meta:
        ordering=('order',)
        unique_together=('fieldname', 'survey')

    def __unicode__(self):
        return self.question

    @property
    def parsed_options(self):
        return filter(None, (s.strip() for s in self.options.splitlines()))


class Submission(models.Model):
    survey=models.ForeignKey(Survey)
    user=models.ForeignKey(User, null=True)
    ip_address=models.IPAddressField()
    submitted_at=models.DateTimeField(default=datetime.datetime.now)
    session_key=models.CharField(max_length=40, blank=True, editable=False)

    # for moderation
    is_public=models.BooleanField(default=True)

    class Meta:
        ordering=('-submitted_at',)

    def to_jsondata(self):
        def to_json(v):
            if isinstance(v, ImageFieldFile):
                return v.url if v else ''
            return v
        return dict(data=dict((a.question.fieldname, to_json(a.value))
                              for a in self.answer_set.filter(question__answer_is_public=True)),
                    submitted_at=self.submitted_at)
    
    def get_answer_dict(self):
        try:
            # avoid called __getattr__
            return self.__dict__['_answer_dict']
        except KeyError:
            answers=self.answer_set.all()
            d=dict((a.question.fieldname, a.value) for a in answers)
            self.__dict__['_answer_dict']=d
            return d

    def __getattr__(self, k):
        d=self.get_answer_dict()
        try:
            return d[k]
        except KeyError:
            raise AttributeError("no such attribute: %s" % k)

    @property
    def email(self):
        return self.get_answer_dict().get('email', '')
        


class Answer(models.Model):
    submission=models.ForeignKey(Submission)
    question=models.ForeignKey(Question)
    text_answer=models.TextField(blank=True)
    integer_answer=models.IntegerField(null=True)
    float_answer=models.FloatField(null=True)
    boolean_answer=models.NullBooleanField()
    image_answer=ImageWithThumbnailsField(max_length=500,
                                          blank=True,
                                          thumbnail=dict(size=(250,250)),
                                          upload_to=local_settings.IMAGE_UPLOAD_PATTERN)
    latitude=models.FloatField(blank=True, null=True)
    longitude=models.FloatField(blank=True, null=True)

    flickr_id=models.CharField(max_length=64, blank=True)
    photo_hash=models.CharField(max_length=40, null=True, blank=True, editable=False)    

    def value():
        def get(self):
            ot=self.question.option_type
            if ot==OPTION_TYPE_CHOICES.BOOLEAN:
                return self.boolean_answer
            elif ot==OPTION_TYPE_CHOICES.FLOAT:
                return self.float_answer
            elif ot==OPTION_TYPE_CHOICES.INTEGER:
                return self.integer_answer
            elif ot==OPTION_TYPE_CHOICES.PHOTO_UPLOAD:
                return self.image_answer
            return self.text_answer
        
        def set(self, v):
            ot=self.question.option_type
            if ot==OPTION_TYPE_CHOICES.BOOLEAN:
                self.boolean_answer=bool(v)
            elif ot==OPTION_TYPE_CHOICES.FLOAT:
                self.float_answer=float(v)
            elif ot==OPTION_TYPE_CHOICES.INTEGER:
                self.integer_answer=int(v)
            elif ot==OPTION_TYPE_CHOICES.PHOTO_UPLOAD:
                self.image_answer=v
            else:
                self.text_answer=v
                
        return get, set
    value=property(*value())

    class Meta:
        ordering=('question',)

    def save(self, **kwargs):
        if sync_to_flickr:
            survey=self.question.survey
            if survey.flickr_set_id:
                try:
                    sync_to_flickr(self, survey.flickr_set_id)
                except:
                    logging.exception("error in syncing to flickr")
                    
        super(Answer, self).save(**kwargs)
    
    def __unicode__(self):
        return unicode(self.question)
