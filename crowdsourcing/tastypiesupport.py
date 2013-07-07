from django.contrib.auth.models import User
from tastypie import fields
from tastypie.api import Api
from tastypie.resources import ModelResource
from tastypie.authentication import Authentication

from .models import Survey, Question
from django.conf.urls import patterns, include, url

class ApiAuthentication(Authentication):
    """
    Only allow access to users that are admin
    """
    def is_authenticated(self, request, **kwargs):
        return request.user.is_staff

    def get_identifier(self, request):
        return request.user.username


class SurveyResource(ModelResource):
    questions = fields.ToManyField('crowdsourcing.tastypiesupport.SurveyQuestionResource', 'questions', full=True)
    class Meta:
        queryset = Survey.objects.all()
        resource_name = 'survey'

class SurveyQuestionResource(ModelResource):
    #survey = fields.ForeignKey('crowdsourcing.tastypiesupport.SurveyResource', 'survey', full=True)
    class Meta:
        queryset = Question.objects.all()
        resource_name = 'question'
        authentication = ApiAuthentication()

# Make the urlpatterns object
v1_api = Api(api_name='v1')
v1_api.register(SurveyQuestionResource())
v1_api.register(SurveyResource())

urlpatterns = patterns('',
    (r'', include(v1_api.urls)),
)
