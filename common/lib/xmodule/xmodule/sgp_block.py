"""
XBlock for Staff Graded Points
"""
from __future__ import absolute_import, division, print_function, unicode_literals

import io
import json
import logging

from crum import get_current_request
from django.middleware.csrf import get_token
from django.utils.translation import ugettext_noop as _

import markdown
from web_fragments.fragment import Fragment
from webob import Response
from xblock.core import XBlock
from xblock.fields import Float, Scope, String
from xblock.runtime import NoSuchServiceError
from xblock.scorable import ScorableXBlockMixin, Score
from xblockutils.resources import ResourceLoader
from xblockutils.studio_editable import StudioEditableXBlockMixin

log = logging.getLogger(__name__)


@XBlock.needs('settings')
@XBlock.needs('grade_utils')
@XBlock.needs('i18n')
@XBlock.needs('user')
class StaffGradedBlock(StudioEditableXBlockMixin, ScorableXBlockMixin, XBlock):
    """
    Staff Graded Points block
    """
    # even though there are no assets, this is required to prevent test breakage
    resources_dir = 'assets/sgp'

    display_name = String(
        display_name=_("Display Name"),
        help=_("The display name for this component."),
        scope=Scope.settings,
        default=_("Staff Graded Points"),
    )
    instructions = String(
        display_name=_("Instructions"),
        help=_("The instructions to the learner. Markdown format"),
        scope=Scope.content,
        multiline_editor=True,
        default=_("Your results will be graded offline"),
        runtime_options={'multiline_editor': 'html'},
    )
    weight = Float(
        display_name="Problem Weight",
        help=_(
            "Enter the number of points possible for this component.  "
            "The default value is 1.0.  "
        ),
        default=1.0,
        scope=Scope.settings,
        values={"min": 0},
    )
    has_score = True

    editable_fields = ('display_name', 'instructions', 'weight')

    def _get_current_username(self):
        return self.runtime.service(self, 'user').get_current_user().opt_attrs.get(
            'edx-platform.username')

    def student_view(self, context):
        """
        Return student view of xblock.
        """
        _ = self.runtime.service(self, "i18n").ugettext

        fragment = Fragment()
        loader = ResourceLoader(__name__)

        context['id'] = self.location.block_id
        context['instructions'] = markdown.markdown(self.instructions)
        context['display_name'] = self.display_name
        context['is_staff'] = self.runtime.user_is_staff

        if context['is_staff']:
            context['import_url'] = self.runtime.handler_url(self, "csv_import_handler")
            context['export_url'] = self.runtime.handler_url(self, "csv_export_handler")
            context['poll_url'] = self.runtime.handler_url(self, "get_results_handler")
            context['csrf_token'] = get_token(get_current_request())

        try:
            score = self.runtime.service(self, "grade_utils").get_score(self.location, self.runtime.user_id) or {}
            context['grades_available'] = True
        except NoSuchServiceError:
            context['grades_available'] = False
        else:
            if score:
                grade = score['grade'] * self.weight
                context['score_string'] = _('{score} / {total} points').format(score=grade, total=self.weight)
            else:
                context['score_string'] = _('{total} points possible').format(total=self.weight)
        fragment.add_content(loader.render_django_template('/templates/html/sgp.html', context))
        return fragment

    @XBlock.handler
    def csv_import_handler(self, request, suffix=''):  # pylint: disable=unused-argument
        """
        Endpoint that handles CSV uploads.
        """
        if not self.runtime.user_is_staff:
            return Response('not allowed', status_code=403)
        grade_utils = self.runtime.service(self, 'grade_utils')

        try:
            score_file = request.POST['csv'].file
        except KeyError:
            data = {'error_rows': [1], 'error_messages': [_('missing file')]}
        else:
            log.info('Processing %d byte score file for %s', score_file.size, self.location)
            block_id = self.location
            block_weight = self.weight
            processor = grade_utils.get_score_processor(block_id=str(block_id), max_points=block_weight)
            processor.process_file(score_file, autocommit=True)
            data = processor.status()

        log.info(data)
        return Response(json.dumps(data), content_type='application/json')

    @XBlock.handler
    def csv_export_handler(self, request, suffix=''):  # pylint: disable=unused-argument
        """
        Endpoint that handles CSV downloads.
        """
        if not self.runtime.user_is_staff:
            return Response('not allowed', status_code=403)

        grade_utils = self.runtime.service(self, 'grade_utils')

        buf = io.BytesIO()
        grade_utils.get_score_processor(
            block_id=str(self.location),
            max_points=self.weight,
            display_name=self.display_name).write_file(buf)
        resp = Response(buf.getvalue())
        resp.content_type = 'text/csv'
        resp.content_disposition = 'attachment; filename="%s.csv"' % self.location
        return resp

    @XBlock.handler
    def get_results_handler(self, request, suffix=''):  # pylint: disable=unused-argument
        """
        Endpoint to poll for celery results.
        """
        if not self.runtime.user_is_staff:
            return Response('not allowed', status_code=403)
        grade_utils = self.runtime.service(self, 'grade_utils')
        try:
            result_id = request.POST['result_id']
        except KeyError:
            data = {'message': 'missing'}
        else:
            results = grade_utils.get_score_processor().get_deferred_result(result_id)
            if results.ready():
                data = results.get()
                log.info('Got results from celery %r', data)
            else:
                data = {'waiting': True, 'result_id': result_id}
                log.info('Still waiting for %s', result_id)
        return Response(json.dumps(data), content_type='application/json')

    def max_score(self):
        return self.weight

    def get_score(self):
        """
        Return a raw score already persisted on the XBlock.  Should not
        perform new calculations.

        Returns:
            Score(raw_earned=float, raw_possible=float)
        """
        score = self.runtime.service(self, "grade_utils").get_score(self.runtime.user_id, self.location)
        score = score or {'grade': 0, 'max_grade': 1}
        return Score(raw_earned=score['grade'], raw_possible=score['max_grade'])

    def set_score(self, score):
        """
        Persist a score to the XBlock.

        The score is a named tuple with a raw_earned attribute and a
        raw_possible attribute, reflecting the raw earned score and the maximum
        raw score the student could have earned respectively.

        Arguments:
            score: Score(raw_earned=float, raw_possible=float)

        Returns:
            None
        """
        state = json.dumps({'grader': self._get_current_username()})
        self.runtime.service(self, "grade_utils").set_score(self.location,
                                                            self.runtime.user_id,
                                                            score.raw_earned,
                                                            score.raw_possible,
                                                            state=state)
        log.info(score)

    def publish_grade(self):
        pass
