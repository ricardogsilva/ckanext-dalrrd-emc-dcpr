import datetime as dt
import logging
import typing

from ckan.plugins import toolkit

from .... import jobs
from ....constants import (
    DcprRequestModerationAction,
    DcprManagementActivityType,
    DCPRRequestStatus,
)
from ... import schema as dcpr_schema
from ....model import dcpr_request
from .... import dcpr_dictization
from .. import create_dcpr_management_activity

logger = logging.getLogger(__name__)


def dcpr_request_update_by_owner(context, data_dict):
    schema = dcpr_schema.update_dcpr_request_by_owner_schema()
    validated_data, errors = toolkit.navl_validate(data_dict, schema, context)
    if errors:
        raise toolkit.ValidationError(errors)
    toolkit.check_access("dcpr_request_update_by_owner_auth", context, validated_data)
    validated_data["owner_user"] = context["auth_user_obj"].id
    context["updated_by"] = "owner"
    request_obj = dcpr_dictization.dcpr_request_dict_save(validated_data, context)
    context["model"].Session.commit()
    create_dcpr_management_activity(
        request_obj,
        activity_type=DcprManagementActivityType.UPDATE_DCPR_REQUEST_BY_OWNER,
        context=context,
    )
    return dcpr_dictization.dcpr_request_dictize(request_obj, context)


def dcpr_request_update_by_nsif(context, data_dict):
    """Update a DCPR request's NSIF-related fields.

    Some fields of a DCPR request can only be modified by members of the NSIF
    organization. Additionally, once a specific user starts updating the request, it
    becomes its nsif_reviewer and all further updates by the NSIF must be done by that
    user.

    """

    schema = dcpr_schema.update_dcpr_request_by_nsif_schema()
    validated_data, errors = toolkit.navl_validate(data_dict, schema, context)
    if errors:
        raise toolkit.ValidationError(errors)
    toolkit.check_access("dcpr_request_update_by_nsif_auth", context, validated_data)
    validated_data.update(
        {
            "nsif_reviewer": context["auth_user_obj"].id,
            "nsif_review_date": dt.datetime.now(dt.timezone.utc),
        }
    )
    request_obj = dcpr_dictization.dcpr_request_dict_save(validated_data, context)
    context["model"].Session.commit()
    create_dcpr_management_activity(
        request_obj,
        activity_type=DcprManagementActivityType.UPDATE_DCPR_REQUEST_BY_NSIF,
        context=context,
    )
    return dcpr_dictization.dcpr_request_dictize(request_obj, context)


def dcpr_request_update_by_csi(context, data_dict):
    """Update a DCPR request's CSI-related fields.

    Some fields of a DCPR request can only be modified by members of the CSI
    organization. Additionally, once a specific user starts updating the request, it
    becomes its csi_moderator and all further updates by the CSI must be done by that
    user.

    """

    schema = dcpr_schema.update_dcpr_request_by_csi_schema()
    validated_data, errors = toolkit.navl_validate(data_dict, schema, context)
    if errors:
        raise toolkit.ValidationError(errors)
    toolkit.check_access("dcpr_request_update_by_csi_auth", context, validated_data)
    validated_data.update(
        {
            "csi_moderator": context["auth_user_obj"].id,
            "csi_moderation_date": dt.datetime.now(dt.timezone.utc),
        }
    )
    request_obj = dcpr_dictization.dcpr_request_dict_save(validated_data, context)
    context["model"].Session.commit()
    create_dcpr_management_activity(
        request_obj,
        activity_type=DcprManagementActivityType.UPDATE_DCPR_REQUEST_BY_CSI,
        context=context,
    )
    return dcpr_dictization.dcpr_request_dictize(request_obj, context)


def dcpr_request_submit(context, data_dict):
    """Submit a DCPR request.

    By submitting a DCPR request, it is marked as ready for review by the SASDI
    organizations.

    """

    schema = dcpr_schema.dcpr_request_submit_schema()
    validated_data, errors = toolkit.navl_validate(data_dict, schema, context)
    if errors:
        raise toolkit.ValidationError(errors)

    toolkit.check_access("dcpr_request_submit_auth", context, validated_data)
    model = context["model"]
    request_obj = model.Session.query(dcpr_request.DCPRRequest).get(
        validated_data["csi_reference_id"]
    )
    if request_obj is not None:
        request_obj.submission_date = dt.datetime.now(dt.timezone.utc)
        _update_dcpr_request_status(request_obj)
        # the sysadmin is authorized to submit a DCPR request - however we want
        # to track that this action has been carried out by the sysadmin and not
        # by the DCPR request's original owner. If the sysadmin submits a DCPR request
        # then it effectively becomes the responsible for the DCPR request, so we make
        # it the owner of the request.
        if context["auth_user_obj"].sysadmin:
            request_obj.owner_user = context["auth_user_obj"].id
            logger.info(
                f"sysadmin {context['auth_user_obj'].id} has now been made the owner "
                f"of DCPR request {request_obj.csi_reference_id}"
            )

        model.Session.commit()
        activity = create_dcpr_management_activity(
            request_obj,
            activity_type=DcprManagementActivityType.SUBMIT_DCPR_REQUEST,
            context=context,
        )
        toolkit.enqueue_job(
            jobs.notify_dcpr_actors_of_relevant_status_change,
            args=[activity["id"]],
        )
    else:
        raise toolkit.ObjectNotFound
    return toolkit.get_action("dcpr_request_show")(context, validated_data)


def dcpr_request_nsif_moderate(
    context: typing.Dict, data_dict: typing.Dict
) -> typing.Dict:
    """Provide the NSIF's moderation for a DCPR request.

    By moderating a DCPR request, it is either rejected or marked as reviewed by the
    NSIF and ready for further moderation by the CSI.

    """

    schema = dcpr_schema.moderate_dcpr_request_schema()
    validated_data, errors = toolkit.navl_validate(data_dict, schema, context)
    if errors:
        raise toolkit.ValidationError(errors)

    toolkit.check_access("dcpr_request_nsif_moderate_auth", context, validated_data)
    validated_data.update(nsif_moderation_date=dt.datetime.now(dt.timezone.utc))
    request_obj = (
        context["model"]
        .Session.query(dcpr_request.DCPRRequest)
        .get(validated_data["csi_reference_id"])
    )
    if request_obj is not None:
        try:
            moderation_action = DcprRequestModerationAction(
                validated_data.get("action")
            )
        except ValueError:
            result = toolkit.abort(status_code=404, detail="Invalid moderation action")
        else:
            _update_dcpr_request_status(
                request_obj, transition_action=moderation_action
            )
            # the sysadmin is authorized to moderate a DCPR request - however we want
            # to track that this action has been carried out by the sysadmin and not
            # by the DCPR request's original NSIF reviewer. As such we change the NSIF
            # reviewer of the DCPR request if the current moderator is a sysadmin
            if context["auth_user_obj"].sysadmin:
                request_obj.nsif_reviewer = context["auth_user_obj"].id
                logger.info(
                    f"sysadmin {context['auth_user_obj'].id} has now been made the "
                    f"NSIF reviewer for DCPR request {request_obj.csi_reference_id}"
                )
            context["model"].Session.commit()
            activity_type = {
                DcprRequestModerationAction.APPROVE: DcprManagementActivityType.ACCEPT_DCPR_REQUEST_NSIF,
                DcprRequestModerationAction.REJECT: DcprManagementActivityType.REJECT_DCPR_REQUEST_NSIF,
                DcprRequestModerationAction.REQUEST_CLARIFICATION: DcprManagementActivityType.REQUEST_CLARIFICATION_DCPR_REQUEST_NSIF,
            }[moderation_action]
            activity = create_dcpr_management_activity(
                request_obj, activity_type=activity_type, context=context
            )
            toolkit.enqueue_job(
                jobs.notify_dcpr_actors_of_relevant_status_change,
                args=[activity["id"]],
            )
            result = toolkit.get_action("dcpr_request_show")(context, validated_data)
    else:
        raise toolkit.ObjectNotFound
    return result


def dcpr_request_csi_moderate(
    context: typing.Dict, data_dict: typing.Dict
) -> typing.Dict:
    schema = dcpr_schema.moderate_dcpr_request_schema()
    validated_data, errors = toolkit.navl_validate(data_dict, schema, context)
    if errors:
        raise toolkit.ValidationError(errors)

    toolkit.check_access("dcpr_request_csi_moderate_auth", context, validated_data)
    validated_data.update(csi_moderation_date=dt.datetime.now(dt.timezone.utc))
    request_obj = (
        context["model"]
        .Session.query(dcpr_request.DCPRRequest)
        .get(validated_data["csi_reference_id"])
    )
    if request_obj is not None:
        try:
            moderation_action = DcprRequestModerationAction(
                validated_data.get("action")
            )
        except ValueError:
            result = toolkit.abort(status_code=404, detail="Invalid moderation action")
        else:
            _update_dcpr_request_status(
                request_obj, transition_action=moderation_action
            )
            # The sysadmin is authorized to moderate a DCPR request - however we want
            # to track that this action has been carried out by the sysadmin and not
            # by the DCPR request's original CSI moderator. As such we change the CSI
            # moderator of the DCPR request if the current moderator is a sysadmin
            if context["auth_user_obj"].sysadmin:
                request_obj.csi_moderator = context["auth_user_obj"].id
                logger.info(
                    f"sysadmin {context['auth_user_obj'].id} has now been made the "
                    f"CSI moderator for DCPR request {request_obj.csi_reference_id}"
                )
            context["model"].Session.commit()
            activity_type = {
                DcprRequestModerationAction.APPROVE: DcprManagementActivityType.ACCEPT_DCPR_REQUEST_CSI,
                DcprRequestModerationAction.REJECT: DcprManagementActivityType.REJECT_DCPR_REQUEST_CSI,
                DcprRequestModerationAction.REQUEST_CLARIFICATION: DcprManagementActivityType.REQUEST_CLARIFICATION_DCPR_REQUEST_CSI,
            }[moderation_action]
            activity = create_dcpr_management_activity(
                request_obj, activity_type=activity_type, context=context
            )
            toolkit.enqueue_job(
                jobs.notify_dcpr_actors_of_relevant_status_change,
                args=[activity["id"]],
            )
            result = toolkit.get_action("dcpr_request_show")(context, validated_data)
    else:
        raise toolkit.ObjectNotFound
    return result


def claim_dcpr_request_nsif_reviewer(
    context: typing.Dict, data_dict: typing.Dict
) -> typing.Dict:
    return _become_reviewer(
        context,
        data_dict,
        auth_function="dcpr_request_claim_nsif_reviewer_auth",
        reviewer_request_attribute="nsif_reviewer",
        activity_type=DcprManagementActivityType.BECOME_NSIF_REVIEWER_DCPR_REQUEST,
    )


def claim_dcpr_request_csi_reviewer(
    context: typing.Dict, data_dict: typing.Dict
) -> typing.Dict:
    return _become_reviewer(
        context,
        data_dict,
        auth_function="dcpr_request_claim_csi_moderator_auth",
        reviewer_request_attribute="csi_moderator",
        activity_type=DcprManagementActivityType.BECOME_CSI_REVIEWER_DCPR_REQUEST,
    )


def _become_reviewer(
    context: typing.Dict,
    data_dict: typing.Dict,
    auth_function: str,
    reviewer_request_attribute: str,
    activity_type: DcprManagementActivityType,
) -> typing.Dict:
    schema = dcpr_schema.claim_reviewer_schema()
    validated_data, errors = toolkit.navl_validate(data_dict, schema, context)
    if errors:
        raise toolkit.ValidationError(errors)

    toolkit.check_access(auth_function, context, validated_data)
    model = context["model"]
    request_obj = (
        context["model"]
        .Session.query(dcpr_request.DCPRRequest)
        .get(validated_data["csi_reference_id"])
    )
    if request_obj is not None:
        _update_dcpr_request_status(request_obj)
        setattr(request_obj, reviewer_request_attribute, context["auth_user_obj"].id)
        model.Session.commit()
    else:
        raise toolkit.ObjectNotFound
    create_dcpr_management_activity(
        request_obj, activity_type=activity_type, context=context
    )
    return toolkit.get_action("dcpr_request_show")(context, validated_data)


def resign_dcpr_request_nsif_reviewer(
    context: typing.Dict, data_dict: typing.Dict
) -> typing.Dict:
    return _resign_reviewer(
        context,
        data_dict,
        auth_function="dcpr_request_resign_nsif_reviewer_auth",
        activity_type=DcprManagementActivityType.RESIGN_NSIF_REVIEWER_DCPR_REQUEST,
    )


def resign_dcpr_request_csi_reviewer(
    context: typing.Dict, data_dict: typing.Dict
) -> typing.Dict:
    return _resign_reviewer(
        context,
        data_dict,
        auth_function="dcpr_request_resign_csi_reviewer_auth",
        activity_type=DcprManagementActivityType.RESIGN_CSI_REVIEWER_DCPR_REQUEST,
    )


def _resign_reviewer(
    context: typing.Dict,
    data_dict: typing.Dict,
    auth_function: str,
    activity_type: DcprManagementActivityType,
) -> typing.Dict:
    schema = dcpr_schema.resign_reviewer_schema()
    validated_data, errors = toolkit.navl_validate(data_dict, schema, context)
    if errors:
        raise toolkit.ValidationError(errors)
    toolkit.check_access(auth_function, context, validated_data)
    request_obj = (
        context["model"]
        .Session.query(dcpr_request.DCPRRequest)
        .get(validated_data["csi_reference_id"])
    )
    if request_obj is not None:
        _update_dcpr_request_status(
            request_obj, transition_action=DcprRequestModerationAction.RESIGN
        )
        context["model"].Session.commit()
    else:
        raise toolkit.ObjectNotFound
    activity = create_dcpr_management_activity(
        request_obj, activity_type=activity_type, context=context
    )
    toolkit.enqueue_job(
        jobs.notify_dcpr_actors_of_relevant_status_change,
        args=[activity["id"]],
    )
    return toolkit.get_action("dcpr_request_show")(context, validated_data)


def _update_dcpr_request_status(
    dcpr_request_obj: dcpr_request.DCPRRequest,
    transition_action: typing.Optional[DcprRequestModerationAction] = None,
) -> dcpr_request.DCPRRequest:
    current_status = DCPRRequestStatus(dcpr_request_obj.status)
    try:
        next_status = _determine_next_dcpr_request_status(
            current_status, transition_action
        )
    except NotImplementedError:
        logger.exception(msg="Unable to update DCPR request status")
    else:
        if next_status is not None:
            dcpr_request_obj.status = next_status.value
    return dcpr_request_obj


def _determine_next_dcpr_request_status(
    current_status: DCPRRequestStatus,
    transition_action: typing.Optional[DcprRequestModerationAction] = None,
) -> typing.Optional[DCPRRequestStatus]:
    # statuses related to request preparation/modification by the owner
    if current_status == DCPRRequestStatus.UNDER_PREPARATION:
        next_status = DCPRRequestStatus.AWAITING_NSIF_REVIEW
    elif current_status == DCPRRequestStatus.UNDER_MODIFICATION_REQUESTED_BY_NSIF:
        next_status = DCPRRequestStatus.UNDER_NSIF_REVIEW
    elif current_status == DCPRRequestStatus.UNDER_MODIFICATION_REQUESTED_BY_CSI:
        next_status = DCPRRequestStatus.UNDER_CSI_REVIEW

    # statuses related to awaiting for a user to claim the role of reviewer
    elif current_status == DCPRRequestStatus.AWAITING_NSIF_REVIEW:
        next_status = DCPRRequestStatus.UNDER_NSIF_REVIEW
    elif current_status == DCPRRequestStatus.AWAITING_CSI_REVIEW:
        next_status = DCPRRequestStatus.UNDER_CSI_REVIEW

    # statuses related to review and moderation
    elif current_status == DCPRRequestStatus.UNDER_NSIF_REVIEW:
        if transition_action == DcprRequestModerationAction.APPROVE:
            next_status = DCPRRequestStatus.AWAITING_CSI_REVIEW
        elif transition_action == DcprRequestModerationAction.REJECT:
            next_status = DCPRRequestStatus.REJECTED
        elif transition_action == DcprRequestModerationAction.REQUEST_CLARIFICATION:
            next_status = DCPRRequestStatus.UNDER_MODIFICATION_REQUESTED_BY_NSIF
        elif transition_action == DcprRequestModerationAction.RESIGN:
            next_status = DCPRRequestStatus.AWAITING_NSIF_REVIEW
        else:
            raise NotImplementedError
    elif current_status == DCPRRequestStatus.UNDER_CSI_REVIEW:
        if transition_action == DcprRequestModerationAction.APPROVE:
            next_status = DCPRRequestStatus.ACCEPTED
        elif transition_action == DcprRequestModerationAction.REJECT:
            next_status = DCPRRequestStatus.REJECTED
        elif transition_action == DcprRequestModerationAction.REQUEST_CLARIFICATION:
            next_status = DCPRRequestStatus.UNDER_MODIFICATION_REQUESTED_BY_CSI
        elif transition_action == DcprRequestModerationAction.RESIGN:
            next_status = DCPRRequestStatus.AWAITING_CSI_REVIEW
        else:
            raise NotImplementedError

    # final statuses
    elif current_status in (DCPRRequestStatus.ACCEPTED, DCPRRequestStatus.REJECTED):
        next_status = None

    else:
        raise NotImplementedError
    return next_status
