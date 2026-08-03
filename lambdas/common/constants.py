"""
XOMFORMS Constants
==================
Ported from xomify-backend's lambdas/common/constants.py convention:
values sourced from environment variables (set by Terraform at deploy time),
with safe local defaults for tests.
"""

import os

AWS_DEFAULT_REGION = 'us-east-1'
AWS_ACCOUNT_ID = os.environ.get('AWS_ACCOUNT_ID', '')
PRODUCT = 'xomforms'

RESPONSE_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization,X-Amz-Date,X-Api-Key,X-Amz-Security-Token",
    "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
    "Content-Type": "application/json",
}

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# ============================================
# DynamoDB
# ============================================
DYNAMODB_KMS_ALIAS = os.environ.get('DYNAMODB_KMS_ALIAS', '')
POLLS_TABLE_NAME = os.environ.get('POLLS_TABLE_NAME', '')
RESPONSES_TABLE_NAME = os.environ.get('RESPONSES_TABLE_NAME', '')

# GSI on xomforms-polls: PK creatorEmail, SK createdAt -- powers "my polls".
POLLS_CREATOR_INDEX = os.environ.get('POLLS_CREATOR_INDEX', 'creatorEmail-createdAt-index')

# ============================================
# Grid / poll config caps
# ============================================
# Bounds enforced in lambdas/common/models.py so a single response item
# (list of selected blockIds) stays comfortably under DynamoDB's 400 KB
# item-size limit even in the worst case (every block selected).
MAX_GRID_BLOCKS = 2000
MAX_DATE_RANGE_DAYS = 60
# Granularity is the creator's "start interval": which start times a responder
# is offered, and therefore the resolution of the paint grid itself. Exposed on
# the create form as On the hour / Every 30 / Every 15. The same tuple still
# gates the LEGACY create shape (dayStart/dayEnd/granularity supplied directly).
ALLOWED_GRANULARITY_MINUTES = (15, 30, 60)
# Fallback when a client omits granularityMinutes on the windowed shape -- keeps
# pre-control clients on exactly the 15-minute grid they used to get.
DEFAULT_GRANULARITY_MINUTES = 15

# Event length ("duration") is now first-class: selectable every 15 minutes
# from 15 up to 360 (6 hours).
MIN_EVENT_DURATION_MINUTES = 15
MAX_EVENT_DURATION_MINUTES = 360
EVENT_DURATION_STEP_MINUTES = 15

# ============================================
# Misc
# ============================================
XOMFORMS_URL = "https://xomforms.xomware.com"
