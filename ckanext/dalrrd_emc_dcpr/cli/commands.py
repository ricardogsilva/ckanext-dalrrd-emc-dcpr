"""CKAN CLI commands for the dalrrd-emc-dcpr extension"""

import contextlib
import datetime as dt
import enum
import io
import inspect
import json
import logging
import os
import typing
from concurrent import futures
from pathlib import Path

import alembic.command
import alembic.config
import alembic.util.exc
import click

import ckan
import ckan.plugins as p
from ckan.plugins import toolkit
from ckan import model
from ckan.lib.navl import dictization_functions

# from ckan.lib.email_notifications import get_and_send_notifications_for_all_users
from ckanext.dalrrd_emc_dcpr.model.dcpr_request import DCPRRequest, init_request_tables

from ..constants import (
    ISO_TOPIC_CATEGOY_VOCABULARY_NAME,
    ISO_TOPIC_CATEGORIES,
    SASDI_THEMES_VOCABULARY_NAME,
)
from ..email_notifications import get_and_send_notifications_for_all_users

from ._bootstrap_data import SASDI_ORGANIZATIONS
from ._sample_datasets import (
    SAMPLE_DATASET_TAG,
    generate_sample_datasets,
)
from ._sample_organizations import SAMPLE_ORGANIZATIONS
from ._sample_users import SAMPLE_USERS
from ._sample_dcpr_requests import SAMPLE_REQUESTS

logger = logging.getLogger(__name__)

_DEFAULT_COLOR: typing.Final[typing.Optional[str]] = None
_SUCCESS_COLOR: typing.Final[str] = "green"
_ERROR_COLOR: typing.Final[str] = "red"
_INFO_COLOR: typing.Final[str] = "yellow"


class DatasetCreationResult(enum.Enum):
    CREATED = "created"
    NOT_CREATED_ALREADY_EXISTS = "already_exists"


@click.group()
def dalrrd_emc_dcpr():
    """Commands related to the dalrrd-emc-dcpr extension."""


@dalrrd_emc_dcpr.command()
def send_email_notifications():
    """Send pending email notifications to users

    This command should be ran periodically.

    """

    setting_key = "ckan.activity_streams_email_notifications"
    if toolkit.asbool(toolkit.config.get(setting_key)):
        env_sentinel = "CKAN_SMTP_PASSWORD"
        if os.getenv(env_sentinel) is not None:
            num_sent = get_and_send_notifications_for_all_users()
            click.secho(f"Sent {num_sent} emails")
            click.secho("Done!", fg=_SUCCESS_COLOR)
        else:
            click.secho(
                f"Could not find the {env_sentinel!r} environment variable. Email "
                f"notifications are not configured correctly. Aborting...",
                fg=_ERROR_COLOR,
            )
    else:
        click.secho(
            f"{setting_key} is not enabled in config. Aborting...", fg=_ERROR_COLOR
        )


@dalrrd_emc_dcpr.group()
def bootstrap():
    """Bootstrap the dalrrd-emc-dcpr extension"""


@dalrrd_emc_dcpr.group()
def delete_data():
    """Delete dalrrd-emc-dcpr bootstrapped and sample data"""


@dalrrd_emc_dcpr.group()
def extra_commands():
    """Extra commands that are less relevant"""


@bootstrap.command()
def create_sasdi_themes():
    """Create SASDI themes

    This command adds a CKAN vocabulary for the SASDI themes and creates each theme
    as a CKAN tag.

    This command can safely be called multiple times - it will only ever create the
    vocabulary and themes once.

    """

    click.secho(
        f"Creating {SASDI_THEMES_VOCABULARY_NAME!r} CKAN tag vocabulary and adding "
        f"configured SASDI themes to it..."
    )

    user = toolkit.get_action("get_site_user")({"ignore_auth": True}, {})
    context = {"user": user["name"]}
    vocab_list = toolkit.get_action("vocabulary_list")(context)
    for voc in vocab_list:
        if voc["name"] == SASDI_THEMES_VOCABULARY_NAME:
            vocabulary = voc
            click.secho(
                (
                    f"Vocabulary {SASDI_THEMES_VOCABULARY_NAME!r} already exists, "
                    f"skipping creation..."
                ),
                fg=_INFO_COLOR,
            )
            break
    else:
        click.echo(f"Creating vocabulary {SASDI_THEMES_VOCABULARY_NAME!r}...")
        vocabulary = toolkit.get_action("vocabulary_create")(
            context, {"name": SASDI_THEMES_VOCABULARY_NAME}
        )

    for theme_name in toolkit.config.get(
        "ckan.dalrrd_emc_dcpr.sasdi_themes"
    ).splitlines():
        if theme_name != "":
            already_exists = theme_name in [tag["name"] for tag in vocabulary["tags"]]
            if not already_exists:
                click.echo(
                    f"Adding tag {theme_name!r} to "
                    f"vocabulary {SASDI_THEMES_VOCABULARY_NAME!r}..."
                )
                toolkit.get_action("tag_create")(
                    context, {"name": theme_name, "vocabulary_id": vocabulary["id"]}
                )
            else:
                click.secho(
                    (
                        f"Tag {theme_name!r} is already part of the "
                        f"{SASDI_THEMES_VOCABULARY_NAME!r} vocabulary, skipping..."
                    ),
                    fg=_INFO_COLOR,
                )
    click.secho("Done!", fg=_SUCCESS_COLOR)


@delete_data.command()
def delete_sasdi_themes():
    """Delete SASDI themes

    This command adds a CKAN vocabulary for the SASDI themes and creates each theme
    as a CKAN tag.

    This command can safely be called multiple times - it will only ever delete the
    vocabulary and themes once, if they exist.

    """

    user = toolkit.get_action("get_site_user")({"ignore_auth": True}, {})
    context = {"user": user["name"]}
    vocabulary_list = toolkit.get_action("vocabulary_list")(context)
    if SASDI_THEMES_VOCABULARY_NAME in [voc["name"] for voc in vocabulary_list]:
        click.secho(
            f"Deleting {SASDI_THEMES_VOCABULARY_NAME!r} CKAN tag vocabulary and "
            f"respective tags... "
        )
        existing_tags = toolkit.get_action("tag_list")(
            context, {"vocabulary_id": SASDI_THEMES_VOCABULARY_NAME}
        )
        for tag_name in existing_tags:
            click.secho(f"Deleting tag {tag_name!r}...")
            toolkit.get_action("tag_delete")(
                context, {"id": tag_name, "vocabulary_id": SASDI_THEMES_VOCABULARY_NAME}
            )
        click.echo(f"Deleting vocabulary {SASDI_THEMES_VOCABULARY_NAME!r}...")
        toolkit.get_action("vocabulary_delete")(
            context, {"id": SASDI_THEMES_VOCABULARY_NAME}
        )
    else:
        click.secho(
            (
                f"Vocabulary {SASDI_THEMES_VOCABULARY_NAME!r} does not exist, "
                f"nothing to do"
            ),
            fg=_INFO_COLOR,
        )
    click.secho(f"Done!", fg=_SUCCESS_COLOR)


@bootstrap.command()
def create_iso_topic_categories():
    """Create ISO Topic Categories.

    This command adds a CKAN vocabulary for the ISO Topic Categories and creates each
    topic category as a CKAN tag.

    This command can safely be called multiple times - it will only ever create the
    vocabulary and themes once.

    """

    click.secho(
        f"Creating ISO Topic Categories CKAN tag vocabulary and adding "
        f"the relevant categories..."
    )

    user = toolkit.get_action("get_site_user")({"ignore_auth": True}, {})
    context = {"user": user["name"]}
    vocab_list = toolkit.get_action("vocabulary_list")(context)
    for voc in vocab_list:
        if voc["name"] == ISO_TOPIC_CATEGOY_VOCABULARY_NAME:
            vocabulary = voc
            click.secho(
                (
                    f"Vocabulary {ISO_TOPIC_CATEGOY_VOCABULARY_NAME!r} already exists, "
                    f"skipping creation..."
                ),
                fg=_INFO_COLOR,
            )
            break
    else:
        click.echo(f"Creating vocabulary {ISO_TOPIC_CATEGOY_VOCABULARY_NAME!r}...")
        vocabulary = toolkit.get_action("vocabulary_create")(
            context, {"name": ISO_TOPIC_CATEGOY_VOCABULARY_NAME}
        )

    for theme_name, _ in ISO_TOPIC_CATEGORIES:
        if theme_name != "":
            already_exists = theme_name in [tag["name"] for tag in vocabulary["tags"]]
            if not already_exists:
                click.echo(
                    f"Adding tag {theme_name!r} to "
                    f"vocabulary {ISO_TOPIC_CATEGOY_VOCABULARY_NAME!r}..."
                )
                toolkit.get_action("tag_create")(
                    context, {"name": theme_name, "vocabulary_id": vocabulary["id"]}
                )
            else:
                click.secho(
                    (
                        f"Tag {theme_name!r} is already part of the "
                        f"{ISO_TOPIC_CATEGOY_VOCABULARY_NAME!r} vocabulary, skipping..."
                    ),
                    fg=_INFO_COLOR,
                )
    click.secho("Done!", fg=_SUCCESS_COLOR)


@delete_data.command()
def delete_iso_topic_categories():
    """Delete ISO Topic Categories.

    This command can safely be called multiple times - it will only ever delete the
    vocabulary and themes once, if they exist.

    """

    user = toolkit.get_action("get_site_user")({"ignore_auth": True}, {})
    context = {"user": user["name"]}
    vocabulary_list = toolkit.get_action("vocabulary_list")(context)
    if ISO_TOPIC_CATEGOY_VOCABULARY_NAME in [voc["name"] for voc in vocabulary_list]:
        click.secho(
            f"Deleting {ISO_TOPIC_CATEGOY_VOCABULARY_NAME!r} CKAN tag vocabulary and "
            f"respective tags... "
        )
        existing_tags = toolkit.get_action("tag_list")(
            context, {"vocabulary_id": ISO_TOPIC_CATEGOY_VOCABULARY_NAME}
        )
        for tag_name in existing_tags:
            click.secho(f"Deleting tag {tag_name!r}...")
            toolkit.get_action("tag_delete")(
                context,
                {"id": tag_name, "vocabulary_id": ISO_TOPIC_CATEGOY_VOCABULARY_NAME},
            )
        click.echo(f"Deleting vocabulary {ISO_TOPIC_CATEGOY_VOCABULARY_NAME!r}...")
        toolkit.get_action("vocabulary_delete")(
            context, {"id": ISO_TOPIC_CATEGOY_VOCABULARY_NAME}
        )
    else:
        click.secho(
            (
                f"Vocabulary {ISO_TOPIC_CATEGOY_VOCABULARY_NAME!r} does not exist, "
                f"nothing to do"
            ),
            fg=_INFO_COLOR,
        )
    click.secho(f"Done!", fg=_SUCCESS_COLOR)


@bootstrap.command()
def create_sasdi_organizations():
    """Create main SASDI organizations

    This command creates the main SASDI organizations.

    This function may fail if the SASDI organizations already exist but are disabled,
    which can happen if they are deleted using the CKAN web frontend.

    This is a product of a CKAN limitation whereby it is not possible to retrieve a
    list of organizations regardless of their status - it will only return those that
    are active.

    """

    existing_organizations = toolkit.get_action("organization_list")(
        context={},
        data_dict={
            "organizations": [org.name for org in SASDI_ORGANIZATIONS],
            "all_fields": False,
        },
    )
    user = toolkit.get_action("get_site_user")({"ignore_auth": True}, {})
    for org_details in SASDI_ORGANIZATIONS:
        if org_details.name not in existing_organizations:
            click.secho(f"Creating organization {org_details.name!r}...")
            try:
                toolkit.get_action("organization_create")(
                    context={
                        "user": user["name"],
                        "return_id_only": True,
                    },
                    data_dict={
                        "name": org_details.name,
                        "title": org_details.title,
                        "description": org_details.description,
                        "image_url": org_details.image_url,
                    },
                )
            except toolkit.ValidationError as exc:
                click.secho(
                    f"Could not create organization {org_details.name!r}: {exc}",
                    fg=_ERROR_COLOR,
                )
    click.secho("Done!", fg=_SUCCESS_COLOR)


@delete_data.command()
def delete_sasdi_organizations():
    """Delete the main SASDI organizations.

    This command will delete the SASDI organizations from the CKAN DB - CKAN refers to
    this as purging the organizations (the CKAN default behavior is to have the delete
    command simply disable the existing organizations, but keeping them in the DB).

    It can safely be called multiple times - it will only ever delete the
    organizations once.

    """

    user = toolkit.get_action("get_site_user")({"ignore_auth": True}, {})
    for org_details in SASDI_ORGANIZATIONS:
        click.secho(f"Purging  organization {org_details.name!r}...")
        try:
            toolkit.get_action("organization_purge")(
                context={"user": user["name"]}, data_dict={"id": org_details.name}
            )
        except toolkit.ObjectNotFound:
            click.secho(
                f"Organization {org_details.name!r} does not exist, skipping...",
                fg=_INFO_COLOR,
            )
    click.secho(f"Done!", fg=_SUCCESS_COLOR)


@bootstrap.command()
def init_dcpr_requests():
    """Initialize database with the DCPR request tables"""
    init_request_tables()


@dalrrd_emc_dcpr.group()
def load_sample_data():
    """Load sample data into non-production deployments"""


@load_sample_data.command()
def create_sample_dcpr_requests():
    """Create sample DCPR requests"""
    user = toolkit.get_action("get_site_user")({"ignore_auth": True}, {})

    convert_user_name_or_id_to_id = toolkit.get_converter(
        "convert_user_name_or_id_to_id"
    )
    user_id = convert_user_name_or_id_to_id(user["name"], {"session": model.Session})

    create_request_action = toolkit.get_action("dcpr_request_create")
    click.secho(f"Creating sample dcpr requests ...")
    for request in SAMPLE_REQUESTS:
        click.secho(f"Creating request with id {request.csi_reference_id!r}...")
        try:
            create_request_action(
                context={
                    "user": user["name"],
                },
                data_dict={
                    "csi_reference_id": request.csi_reference_id,
                    "owner_user": user_id,
                    "csi_moderator": user_id,
                    "nsif_reviewer": user_id,
                    "notification_targets": [{"user_id": user_id, "group_id": None}],
                    "status": request.status,
                    "organization_name": request.organization_name,
                    "organization_level": request.organization_level,
                    "organization_address": request.organization_address,
                    "proposed_project_name": request.proposed_project_name,
                    "additional_project_context": request.additional_project_context,
                    "capture_start_date": request.capture_start_date,
                    "capture_end_date": request.capture_end_date,
                    "cost": request.cost,
                    "spatial_extent": request.spatial_extent,
                    "spatial_resolution": request.spatial_resolution,
                    "data_capture_urgency": request.data_capture_urgency,
                    "additional_information": request.additional_information,
                    "request_date": request.request_date,
                    "submission_date": request.submission_date,
                    "nsif_review_date": request.nsif_review_date,
                    "nsif_recommendation": request.nsif_recommendation,
                    "nsif_review_notes": request.nsif_review_notes,
                    "nsif_review_additional_documents": request.nsif_review_additional_documents,
                    "csi_moderation_notes": request.csi_moderation_notes,
                    "csi_moderation_additional_documents": request.csi_moderation_additional_documents,
                    "csi_moderation_date": request.csi_moderation_date,
                    "dataset_custodian": request.dataset_custodian,
                    "data_type": request.data_type,
                    "purposed_dataset_title": request.purposed_dataset_title,
                    "purposed_abstract": request.purposed_abstract,
                    "dataset_purpose": request.dataset_purpose,
                    "lineage_statement": request.lineage_statement,
                    "associated_attributes": request.associated_attributes,
                    "feature_description": request.feature_description,
                    "data_usage_restrictions": request.data_usage_restrictions,
                    "capture_method": request.capture_method,
                    "capture_method_detail": request.capture_method_detail,
                },
            )
        except toolkit.ValidationError as exc:
            click.secho(
                f"Could not create request with id {request.csi_reference_id!r}: {exc}",
                fg=_INFO_COLOR,
            )
            click.secho(
                f"Attempting to re-enable possibly deleted request...", fg=_INFO_COLOR
            )
            sample_request = DCPRRequest.get(request.id)
            if sample_request is None:
                click.secho(
                    f"Could not find sample request with id {request.csi_reference_id!r}",
                    fg=_ERROR_COLOR,
                )
                continue
            else:
                sample_request.undelete()
                model.repo.commit()


@load_sample_data.command()
def create_sample_users():
    user = toolkit.get_action("get_site_user")({"ignore_auth": True}, {})
    create_user_action = toolkit.get_action("user_create")
    click.secho(f"Creating sample users ...")
    for user_details in SAMPLE_USERS:
        click.secho(f"Creating {user_details.name!r}...")
        try:
            create_user_action(
                context={
                    "user": user["name"],
                },
                data_dict={
                    "name": user_details.name,
                    "email": user_details.email,
                    "password": user_details.password,
                },
            )
        except toolkit.ValidationError as exc:
            click.secho(
                f"Could not create user {user_details.name!r}: {exc}", fg=_INFO_COLOR
            )
            click.secho(
                f"Attempting to re-enable possibly deleted user...", fg=_INFO_COLOR
            )
            sample_user = model.User.get(user_details.name)
            if sample_user is None:
                click.secho(
                    f"Could not find sample_user {user_details.name!r}", fg=_ERROR_COLOR
                )
                continue
            else:
                sample_user.undelete()
                model.repo.commit()


@load_sample_data.command()
def create_sample_organizations():
    """Create sample organizations and members"""
    user = toolkit.get_action("get_site_user")({"ignore_auth": True}, {})
    create_org_action = toolkit.get_action("organization_create")
    create_org_member_action = toolkit.get_action("organization_member_create")
    create_harvester_action = toolkit.get_action("harvest_source_create")
    click.secho(f"Creating sample organizations ...")
    for org_details, memberships, harvesters in SAMPLE_ORGANIZATIONS:
        click.secho(f"Creating {org_details.name!r}...")
        try:
            create_org_action(
                context={
                    "user": user["name"],
                },
                data_dict={
                    "name": org_details.name,
                    "title": org_details.title,
                    "description": org_details.description,
                    "image_url": org_details.image_url,
                },
            )
        except toolkit.ValidationError as exc:
            click.secho(
                f"Could not create organization {org_details.name!r}: {exc}",
                fg=_ERROR_COLOR,
            )
        for user_name, role in memberships:
            click.secho(f"Creating membership {user_name!r} ({role!r})...")
            create_org_member_action(
                context={
                    "user": user["name"],
                },
                data_dict={
                    "id": org_details.name,
                    "username": user_name,
                    "role": role if role != "publisher" else "admin",
                },
            )
        for harvester_details in harvesters:
            click.secho(f"Creating harvest source {harvester_details.name!r}...")
            try:
                create_harvester_action(
                    context={"user": user["name"]},
                    data_dict={
                        "name": harvester_details.name,
                        "url": harvester_details.url,
                        "source_type": harvester_details.source_type,
                        "frequency": harvester_details.update_frequency,
                        "config": json.dumps(harvester_details.configuration),
                        "owner_org": org_details.name,
                    },
                )
            except toolkit.ValidationError as exc:
                click.secho(
                    (
                        f"Could not create harvest source "
                        f"{harvester_details.name!r}: {exc}"
                    ),
                    fg=_INFO_COLOR,
                )
                click.secho(
                    f"Attempting to re-enable possibly deleted harvester source...",
                    fg=_INFO_COLOR,
                )
                sample_harvester = model.Package.get(harvester_details.name)
                if sample_harvester is None:
                    click.secho(
                        f"Could not find harvester source {harvester_details.name!r}",
                        fg=_ERROR_COLOR,
                    )
                    continue
                else:
                    sample_harvester.state = model.State.ACTIVE
                    model.repo.commit()
    click.secho("Done!", fg=_SUCCESS_COLOR)


@delete_data.command()
def delete_sample_users():
    """Delete sample users."""
    user = toolkit.get_action("get_site_user")({"ignore_auth": True}, {})
    delete_user_action = toolkit.get_action("user_delete")
    click.secho(f"Deleting sample users ...")
    for user_details in SAMPLE_USERS:
        click.secho(f"Deleting {user_details.name!r}...")
        delete_user_action(
            context={"user": user["name"]},
            data_dict={"id": user_details.name},
        )
    click.secho("Done!", fg=_SUCCESS_COLOR)


@delete_data.command()
def delete_sample_organizations():
    """Delete sample organizations."""
    user = toolkit.get_action("get_site_user")({"ignore_auth": True}, {})

    org_show_action = toolkit.get_action("organization_show")
    purge_org_action = toolkit.get_action("organization_purge")
    package_search_action = toolkit.get_action("package_search")
    dataset_purge_action = toolkit.get_action("dataset_purge")
    harvest_source_list_action = toolkit.get_action("harvest_source_list")
    harvest_source_delete_action = toolkit.get_action("harvest_source_delete")
    click.secho(f"Purging sample organizations ...")
    for org_details, _, _ in SAMPLE_ORGANIZATIONS:
        try:
            org = org_show_action(
                context={"user": user["name"]}, data_dict={"id": org_details.name}
            )
            click.secho(f"{org = }", fg=_INFO_COLOR)
        except toolkit.ObjectNotFound:
            click.secho(
                f"Organization {org_details.name} does not exist, skipping...",
                fg=_INFO_COLOR,
            )
        else:
            packages = package_search_action(
                context={"user": user["name"]},
                data_dict={"fq": f"owner_org:{org['id']}"},
            )
            click.secho(f"{packages = }", fg=_INFO_COLOR)
            for package in packages["results"]:
                click.secho(f"Purging package {package['id']}...")
                dataset_purge_action(
                    context={"user": user["name"]}, data_dict={"id": package["id"]}
                )
            harvest_sources = harvest_source_list_action(
                context={"user": user["name"]}, data_dict={"organization_id": org["id"]}
            )
            click.secho(f"{ harvest_sources = }", fg=_INFO_COLOR)
            for harvest_source in harvest_sources:
                click.secho(f"Deleting harvest_source {harvest_source['title']}...")
                harvest_source_delete_action(
                    context={"user": user["name"], "clear_source": True},
                    data_dict={"id": harvest_source["id"]},
                )
            click.secho(f"Purging {org_details.name!r}...")
            purge_org_action(
                context={"user": user["name"]},
                data_dict={"id": org["id"]},
            )
    click.secho("Done!", fg=_SUCCESS_COLOR)


@load_sample_data.command()
@click.argument("owner_org")
@click.option("-n", "--num-datasets", default=10, show_default=True)
@click.option("-p", "--name-prefix", default="sample-dataset", show_default=True)
@click.option("-s", "--name-suffix")
@click.option(
    "-t",
    "--temporal-range",
    nargs=2,
    type=click.DateTime(),
    default=(dt.datetime(2021, 1, 1), dt.datetime(2022, 12, 31)),
)
@click.option("-x", "--longitude-range", nargs=2, type=float, default=(16.3, 33.0))
@click.option("-y", "--latitude-range", nargs=2, type=float, default=(-35.0, -21.0))
def create_sample_datasets(
    owner_org,
    num_datasets,
    name_prefix,
    name_suffix,
    temporal_range,
    longitude_range,
    latitude_range,
):
    """Create multiple sample datasets"""
    user = toolkit.get_action("get_site_user")({"ignore_auth": True}, {})
    datasets = generate_sample_datasets(
        num_datasets,
        name_prefix,
        owner_org,
        name_suffix,
        temporal_range_start=temporal_range[0],
        temporal_range_end=temporal_range[1],
        longitude_range_start=longitude_range[0],
        longitude_range_end=longitude_range[1],
        latitude_range_start=latitude_range[0],
        latitude_range_end=latitude_range[1],
    )
    ready_to_create_datasets = [ds.to_data_dict() for ds in datasets]
    workers = min(3, len(ready_to_create_datasets))
    with futures.ThreadPoolExecutor(workers) as executor:
        to_do = []
        for dataset in ready_to_create_datasets:
            future = executor.submit(_create_single_dataset, user, dataset)
            to_do.append(future)
        num_created = 0
        num_already_exist = 0
        num_failed = 0
        for done_future in futures.as_completed(to_do):
            try:
                result = done_future.result()
                if result == DatasetCreationResult.CREATED:
                    num_created += 1
                elif result == DatasetCreationResult.NOT_CREATED_ALREADY_EXISTS:
                    num_already_exist += 1
            except dictization_functions.DataError as exc:
                click.secho(f"Could not create dataset: {exc=}", fg=_ERROR_COLOR)
                num_failed += 1
            except ValueError as exc:
                click.secho(f"Could not create dataset: {exc=}", fg=_ERROR_COLOR)
                num_failed += 1

    click.secho(f"Created {num_created} datasets", fg=_INFO_COLOR)
    click.secho(f"Skipped {num_already_exist} datasets", fg=_INFO_COLOR)
    click.secho(f"Failed to create {num_failed} datasets", fg=_INFO_COLOR)
    click.secho("Done!", fg=_SUCCESS_COLOR)


def _create_single_dataset(
    user: typing.Dict, dataset: typing.Dict
) -> DatasetCreationResult:
    create_dataset_action = toolkit.get_action("package_create")
    get_dataset_action = toolkit.get_action("package_show")
    try:
        get_dataset_action(
            context={"user": user["name"]}, data_dict={"id": dataset["name"]}
        )
    except toolkit.ObjectNotFound:
        package_exists = False
    else:
        package_exists = True
    if not package_exists:
        create_dataset_action(context={"user": user["name"]}, data_dict=dataset)
        result = DatasetCreationResult.CREATED
    else:
        result = DatasetCreationResult.NOT_CREATED_ALREADY_EXISTS
    return result


# TODO: speed this up by doing concurrent processing, similar to create_sample_datasets
@delete_data.command()
def delete_sample_datasets():
    """Deletes at most 1000 of existing sample datasets"""
    user = toolkit.get_action("get_site_user")({"ignore_auth": True}, {})
    purge_dataset_action = toolkit.get_action("dataset_purge")
    get_datasets_action = toolkit.get_action("package_search")
    max_rows = 1000
    existing_sample_datasets = get_datasets_action(
        context={"user": user["name"]},
        data_dict={
            "q": f"tags:{SAMPLE_DATASET_TAG}",
            "rows": max_rows,
            "facet": False,
            "include_drafts": True,
            "include_private": True,
        },
    )
    for dataset in existing_sample_datasets["results"]:
        click.secho(f"Purging dataset {dataset['name']!r}...")
        purge_dataset_action(
            context={"user": user["name"]}, data_dict={"id": dataset["id"]}
        )
    num_existing = existing_sample_datasets["count"]
    remaining_sample_datasets = num_existing - max_rows
    if remaining_sample_datasets > 0:
        click.secho(f"{remaining_sample_datasets} still remain", fg=_INFO_COLOR)
    click.secho("Done!", fg=_SUCCESS_COLOR)


# TODO: This command does not need to be needed anymore,
#  since the vanilla ckan command seems to work - leaving t here in case we
#  eventually need it
@extra_commands.command()
@click.argument("plugin_name")
@click.option("-m", "--message")
@click.option("-a", "--autogenerate", is_flag=True)
@click.option("-h", "--head")
def add_db_revision(plugin_name, message, autogenerate, head):
    try:
        alembic_wrapper = AlembicWrapper(plugin_name)
    except RuntimeError:
        click.secho(
            f"Plugin {plugin_name!r} does not seem to use migrations", fg=_ERROR_COLOR
        )
    else:
        if head is None:
            head = f"{plugin_name}@head"
        alembic_config_ini = Path(_resolve_alembic_config(plugin_name))
        version_path = str(alembic_config_ini.parent / "versions")
        click.secho(f"{version_path=}", fg=_INFO_COLOR)
        out = alembic_wrapper.run_command(
            alembic.command.revision,
            message=message,
            head=head,
            # branch_label=plugin_name,
            version_path=version_path,
            autogenerate=autogenerate,
        )
        click.secho(f"{out=}", fg=_INFO_COLOR)


def _resolve_alembic_config(plugin):
    if plugin:
        plugin_obj = p.get_plugin(plugin)
        if plugin_obj is None:
            toolkit.error_shout("Plugin '{}' cannot be loaded.".format(plugin))
            raise click.Abort()
        plugin_dir = os.path.dirname(inspect.getsourcefile(type(plugin_obj)))

        # if there is `plugin` folder instead of single_file, find
        # plugin's parent dir
        ckanext_idx = plugin_dir.rfind("/ckanext/") + 9
        idx = plugin_dir.find("/", ckanext_idx)
        if ~idx:
            plugin_dir = plugin_dir[:idx]
        migration_dir = os.path.join(plugin_dir, "migration", plugin)
    else:
        import ckan.migration as _cm

        migration_dir = os.path.dirname(_cm.__file__)
    return os.path.join(migration_dir, "alembic.ini")


class AlembicWrapper:
    alembic_conf: alembic.config.Config
    _command_output: typing.List[str]

    def __init__(self, plugin_name):
        self.alembic_conf = self._get_alembic_config(plugin_name)
        self._command_output = []

    def run_command(self, alembic_command, *args, **kwargs):
        current_output_index = len(self._command_output)
        alembic_command(self.alembic_conf, *args, **kwargs)
        return self._command_output[current_output_index:]

    def _capture_alembic_output(self, message: str, *args):
        message = message % args
        self._command_output.append(message)

    def _get_alembic_config(self, plugin_name: str):
        alembic_config_ini = Path(_resolve_alembic_config(plugin_name))
        ckan_versions_path = str(Path(ckan.__file__).parent / "migration/versions")
        if alembic_config_ini.exists():
            conf = alembic.config.Config(str(alembic_config_ini))
            conf.set_main_option("script_location", str(alembic_config_ini.parent))
            conf.set_main_option("sqlalchemy.url", toolkit.config.get("sqlalchemy.url"))
            conf.set_main_option(
                "version_locations",
                " ".join((f"%(here)s/versions", ckan_versions_path)),
            )
            conf.print_stdout = self._capture_alembic_output
            click.secho(
                f"version_locations in the config: {conf.get_main_option('version_locations')}",
                fg=_INFO_COLOR,
            )
        else:
            raise RuntimeError("Input plugin name does not have alembic config")
        return conf
