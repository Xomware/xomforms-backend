"""
GET /polls/list -- "My polls" via the creatorEmail-createdAt GSI (authed).
"""

from lambdas.common.logger import get_logger
from lambdas.common.errors import handle_errors
from lambdas.common.utility_helpers import success_response, get_caller_email
from lambdas.common.polls_dynamo import query_polls_by_creator

log = get_logger(__file__)

HANDLER = "polls_list"


@handle_errors(HANDLER)
def handler(event, context):
    email = get_caller_email(event)
    polls = query_polls_by_creator(email)
    return success_response({"polls": polls})
