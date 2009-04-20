import re

from django.conf import settings as _gs


MODERATE_SUBMISSIONS=getattr(_gs, 'CROWDSOURCING_MODERATE_SUBMISSIONS', False)


VIDEO_URL_PATTERNS=getattr(_gs, 'CROWDSOURCING_VIDEO_URL_PATTERNS',
                           (r'^http://www\.youtube\.com/watch\?v=[a-zA-Z0-9]+$',)
                           )


IMAGE_UPLOAD_PATTERN=getattr(_gs, 'CROWDSOURCING_IMAGE_UPLOAD_PATTERN',
                             'crowdsourcing/images/%Y/%m/%d')


FLICKR_API_KEY=getattr(_gs, 'CROWDSOURCING_FLICKR_API_KEY', '')


FLICKR_API_SECRET=getattr(_gs, 'CROWDSOURCING_FLICKR_API_SECRET', '')


FLICKR_TOKEN=getattr(_gs, 'CROWDSOURCING_FLICKR_TOKEN', '')


FLICKR_FROB=getattr(_gs, 'CROWDSOURCING_FLICKR_FROB', '')


FLICKR_LIVE=getattr(_gs, 'CROWDSOURCING_FLICKR_LIVE', False)