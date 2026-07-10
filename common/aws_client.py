"""
AWS session/client factory for the Job Remediation AI Agent's executor.

Deliberately does NOT invent a custom credentials system - it uses
boto3's standard credential resolution chain, which (in priority order)
checks: explicit AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY env vars, a
named profile (AWS_PROFILE) from ~/.aws/credentials, an EC2/ECS/Lambda
instance role, or SSO. This is the same approach the AWS CLI uses, so
`aws configure` / `aws sso login` "just works" for this project too.

See the README's "AWS Prerequisites" section for the IAM permissions
this project's remediation actions need, and for how to set up
credentials (env vars vs named profile vs IAM role).
"""
import logging
import os

import boto3

logger = logging.getLogger(__name__)

_session = None
_clients = {}


def get_session() -> boto3.Session:
    global _session
    if _session is None:
        region = os.getenv("AWS_REGION", "us-east-1")
        profile = os.getenv("AWS_PROFILE") or None
        if profile:
            logger.info("Using AWS profile '%s' in region '%s'", profile, region)
            _session = boto3.Session(profile_name=profile, region_name=region)
        else:
            logger.info("Using default AWS credential chain in region '%s'", region)
            _session = boto3.Session(region_name=region)
    return _session


def get_client(service_name: str):
    """Lazily creates (and caches) a boto3 client for the given service.
    Lazy on purpose: importing this module must not fail just because AWS
    credentials aren't configured yet - only actually executing a
    remediation action should require valid credentials."""
    if service_name not in _clients:
        _clients[service_name] = get_session().client(service_name)
    return _clients[service_name]
