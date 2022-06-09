import logging
import typing

from ckan.plugins import toolkit
from ...model import dcpr_request as dcpr_request
from ...constants import DCPRRequestStatus, CSI_ORG_NAME, NSIF_ORG_NAME

logger = logging.getLogger(__name__)


def my_dcpr_request_list_auth(
    context: typing.Dict, data_dict: typing.Optional[typing.Dict] = None
):
    return {"success": True}


def dcpr_request_list_private_auth(
    context: typing.Dict, data_dict: typing.Optional[typing.Dict] = None
):
    """Authorize listing private DCPR requests.

    Only users that are members of an organization are allowed to have private DCPR
    requests.

    """
    return dcpr_request_create_auth(context, data_dict)


@toolkit.auth_allow_anonymous_access
def dcpr_request_list_public_auth(
    context: typing.Dict, data_dict: typing.Optional[typing.Dict] = None
) -> typing.Dict:
    """Authorize listing public DCPR requests"""
    return {"success": True}


def dcpr_request_list_pending_csi_auth(
    context: typing.Dict, data_dict: typing.Optional[typing.Dict] = None
):
    """Authorize listing DCPR requests which are under evaluation by CSI"""
    return {
        "success": toolkit.h["emc_user_is_org_member"](
            CSI_ORG_NAME, context["auth_user_obj"]
        )
    }


def dcpr_request_list_pending_nsif_auth(
    context: typing.Dict, data_dict: typing.Optional[typing.Dict] = None
):
    """Authorize listing DCPR requests which are under evaluation by NSIF"""
    return {
        "success": toolkit.h["emc_user_is_org_member"](
            NSIF_ORG_NAME, context["auth_user_obj"]
        )
    }


def dcpr_report_create_auth(
    context: typing.Dict, data_dict: typing.Optional[typing.Dict] = None
) -> typing.Dict:
    logger.debug("Inside the dcpr_report_create auth")
    user = context["auth_user_obj"]

    # Only allow creation of dcpr report if there is a user logged in.
    if user:
        return {"success": True}
    return {"success": False}


def dcpr_request_create_auth(
    context: typing.Dict, data_dict: typing.Optional[typing.Dict] = None
) -> typing.Dict:
    """Authorize DCPR request creation.

    Creation of DCPR requests is reserved for logged in users that have been granted
    membership of an organization.

    NOTE: The implementation does not need to check if the user is logged in because
    CKAN already does that for us, as per:

    https://docs.ckan.org/en/2.9/extensions/plugin-interfaces.html#ckan.plugins.interfaces.IAuthFunctions

    """

    db_user = context["auth_user_obj"]
    member_of_orgs = len(db_user.get_groups()) > 0
    result = {"success": member_of_orgs}
    return result


@toolkit.auth_allow_anonymous_access
def dcpr_request_show_auth(context: typing.Dict, data_dict: typing.Dict) -> typing.Dict:
    result = {"success": False}
    request_obj = dcpr_request.DCPRRequest.get(data_dict.get("csi_reference_id"))
    if request_obj is not None:
        unauthorized_msg = toolkit._("You are not authorized to view this request")
        published_statuses = (
            DCPRRequestStatus.ACCEPTED.value,
            DCPRRequestStatus.REJECTED.value,
        )
        csi_statuses = (
            DCPRRequestStatus.AWAITING_CSI_REVIEW.value,
            DCPRRequestStatus.UNDER_CSI_REVIEW.value,
        )
        nsif_statuses = (
            DCPRRequestStatus.AWAITING_NSIF_REVIEW.value,
            DCPRRequestStatus.UNDER_NSIF_REVIEW.value,
        )
        auth_user_obj = context["auth_user_obj"]
        if auth_user_obj is not None and auth_user_obj.id == request_obj.owner_user:
            # this is the owner, allow regardless of current status
            result["success"] = True
        elif request_obj.status in published_statuses:
            # allow, request has already been moderated so everyone can see it
            result["success"] = True
        elif request_obj.status == DCPRRequestStatus.UNDER_PREPARATION.value:
            # user is not the owner and the request has not been submitted yet, deny
            result["msg"] = unauthorized_msg
        elif request_obj.status in csi_statuses:
            # user is not the owner, but if it is member of CSI, then allow
            is_csi_member = toolkit.h["emc_user_is_org_member"](
                CSI_ORG_NAME, context["auth_user_obj"]
            )
            if is_csi_member:
                result["success"] = True
            else:
                result["msg"] = unauthorized_msg
        elif request_obj.status in nsif_statuses:
            # user is not the owner, but if it is member of NSIF, then allow
            is_nsif_member = toolkit.h["emc_user_is_org_member"](
                NSIF_ORG_NAME, context["auth_user_obj"]
            )
            if is_nsif_member:
                result["success"] = True
            else:
                result["msg"] = unauthorized_msg
    else:
        result["msg"] = toolkit._("DCPR request not found")
    return result


def dcpr_request_update_by_owner_auth(
    context: typing.Dict, data_dict: typing.Dict
) -> typing.Dict:
    request_obj = dcpr_request.DCPRRequest.get(data_dict["csi_reference_id"])
    result = {"success": False}
    if request_obj is not None:
        owner_updatable_statuses = [
            DCPRRequestStatus.UNDER_PREPARATION.value,
            DCPRRequestStatus.UNDER_MODIFICATION_REQUESTED_BY_NSIF.value,
            DCPRRequestStatus.UNDER_MODIFICATION_REQUESTED_BY_CSI.value,
        ]
        if request_obj.status in owner_updatable_statuses:
            if context["auth_user_obj"].id == request_obj.owner_user:
                result["success"] = True
            else:
                result["msg"] = toolkit._(
                    "Current user is not authorized to update this DCPR request"
                )
        else:
            result["msg"] = toolkit._("DCPR request cannot currently be updated")
    else:
        result["msg"] = toolkit._("Request not found")
    return result


def dcpr_request_update_by_nsif_auth(
    context: typing.Dict, data_dict: typing.Dict
) -> typing.Dict:
    """Authorize updates of a DCPR request's NSIF-related fields.

    In order for a DCPR request to have its NSIF-related fields be updated, the
    following conditions must be met:

    - Current status of the DCPR request must be under NSIF moderation and the current
      user is the reviewer

    """

    result = {"success": False}
    request_obj = dcpr_request.DCPRRequest.get(data_dict.get("csi_reference_id"))
    if request_obj is not None:
        if request_obj.status == DCPRRequestStatus.UNDER_NSIF_REVIEW.value:
            is_reviewer = request_obj.nsif_reviewer == context["auth_user_obj"].id
            if is_reviewer:
                result["success"] = True
            else:
                result["msg"] = toolkit._(
                    "You are not authorized to update this request"
                )
        else:
            result["msg"] = toolkit._(
                "DCPR request cannot be currently updated by NSIF members"
            )
    else:
        result["msg"] = toolkit._("DCPR request not found")
    return result


def dcpr_request_update_by_csi_auth(
    context: typing.Dict, data_dict: typing.Dict
) -> typing.Dict:
    """Authorize updates of a DCPR request's CSI-related fields.

    In order for a DCPR request to have its CSI-related fields be updated, the following
    conditions must be met:

    - Current user must be member of the CSI organization;
    - Current status of the DCPR request must be awaiting CSI moderation
    - Current status of the DCPR request must be under CSI moderation and the current
      user is the moderator

    """

    result = {"success": False}
    request_obj = dcpr_request.DCPRRequest.get(data_dict.get("csi_reference_id"))
    if request_obj is not None:
        if request_obj.status == DCPRRequestStatus.UNDER_CSI_REVIEW.value:
            is_moderator = request_obj.csi_moderator == context["auth_user_obj"].id
            if is_moderator:
                result["success"] = True
            else:
                result["msg"] = toolkit._(
                    "You are not authorized to update this request"
                )
        else:
            result["msg"] = toolkit._(
                "DCPR request cannot be currently updated by CSI members"
            )
    else:
        result["msg"] = toolkit._("DCPR request not found")
    return result


def dcpr_request_submit_auth(
    context: typing.Dict, data_dict: typing.Dict
) -> typing.Dict:
    """DCPR request owners are the only users that are authorized to submit a request"""
    return dcpr_request_update_by_owner_auth(context, data_dict)


def dcpr_request_nsif_moderate_auth(
    context: typing.Dict, data_dict: typing.Dict
) -> typing.Dict:
    """DCPR request nsif_reviewer is the only one allowed to moderate it."""
    request_obj = dcpr_request.DCPRRequest.get(data_dict["csi_reference_id"])
    result = {"success": False}
    if request_obj is not None:
        if request_obj.status == DCPRRequestStatus.UNDER_NSIF_REVIEW.value:
            if context["auth_user_obj"].id == request_obj.nsif_reviewer:
                result["success"] = True
            else:
                result["msg"] = toolkit._(
                    "Current user is not authorized to moderate this DCPR request on "
                    "behalf of the NSIF"
                )
        else:
            result["msg"] = toolkit._(
                "DCPR request cannot currently be moderated on behalf of the NSIF"
            )
    else:
        result["msg"] = toolkit._("Request not found")
    return result


def dcpr_request_csi_moderate_auth(
    context: typing.Dict, data_dict: typing.Dict
) -> typing.Dict:
    """DCPR request csi_moderator is the only one allowed to moderate it."""
    request_obj = dcpr_request.DCPRRequest.get(data_dict["csi_reference_id"])
    result = {"success": False}
    if request_obj is not None:
        if request_obj.status == DCPRRequestStatus.UNDER_CSI_REVIEW.value:
            if context["auth_user_obj"].id == request_obj.csi_moderator:
                result["success"] = True
            else:
                result["msg"] = toolkit._(
                    "Current user is not authorized to moderate this DCPR request on "
                    "behalf of the CSI"
                )
        else:
            result["msg"] = toolkit._(
                "DCPR request cannot currently be moderated on behalf of the CSI"
            )
    else:
        result["msg"] = toolkit._("Request not found")
    return result


def dcpr_request_delete_auth(
    context: typing.Dict, data_dict: typing.Optional[typing.Dict] = None
) -> typing.Dict:
    """
    DCPR requests shall be deletable by their creator, but only if they are not
    submitted yet.

    """

    request_id = toolkit.get_or_bust(data_dict, "csi_reference_id")
    request_obj = dcpr_request.DCPRRequest.get(request_id)
    result = {"success": False}
    if request_obj is not None:
        is_owner = context["auth_user_obj"].id == request_obj.owner_user
        request_in_preparation = (
            request_obj.status == DCPRRequestStatus.UNDER_PREPARATION.value
        )
        if is_owner and request_in_preparation:
            result["success"] = True
    else:
        result["msg"] = toolkit._("Request not found")
    return result


def dcpr_request_claim_nsif_reviewer_auth(
    context: typing.Dict, data_dict: typing.Optional[typing.Dict] = None
) -> typing.Dict:
    """Check whether current user can claim the role of NSIF reviewer for a DCPR request"""
    request_id = toolkit.get_or_bust(data_dict, "csi_reference_id")
    request_obj = dcpr_request.DCPRRequest.get(request_id)
    result = {"success": False}
    if request_obj is not None:
        is_nsif_member = toolkit.h["emc_user_is_org_member"](
            NSIF_ORG_NAME, context["auth_user_obj"]
        )
        if is_nsif_member:
            if request_obj.status == DCPRRequestStatus.AWAITING_NSIF_REVIEW.value:
                result["success"] = True
            else:
                result["msg"] = toolkit._(
                    "DCPR request cannot currently be claimed for NSIF review"
                )
        else:
            result["msg"] = toolkit._(
                "Current user is not authorized to claim the role of NSIF reviewer "
                "for this DCPR request"
            )
    else:
        result["msg"] = toolkit._("Request not found")
    return result


def dcpr_request_claim_csi_moderator_auth(
    context: typing.Dict, data_dict: typing.Optional[typing.Dict] = None
) -> typing.Dict:
    """Check whether current user can claim the role of CSI moderator for a DCPR request"""
    request_id = toolkit.get_or_bust(data_dict, "csi_reference_id")
    request_obj = dcpr_request.DCPRRequest.get(request_id)
    result = {"success": False}
    if request_obj is not None:
        is_csi_member = toolkit.h["emc_user_is_org_member"](
            CSI_ORG_NAME, context["auth_user_obj"]
        )
        if is_csi_member:
            if request_obj.status == DCPRRequestStatus.AWAITING_CSI_REVIEW.value:
                result["success"] = True
            else:
                result["msg"] = toolkit._(
                    "DCPR request cannot currently be claimed for CSI review"
                )
        else:
            result["msg"] = toolkit._(
                "Current user is not authorized to claim the role of CSI moderator "
                "for this DCPR request"
            )
    else:
        result["msg"] = toolkit._("Request not found")
    return result


def dcpr_request_resign_nsif_reviewer_auth(
    context: typing.Dict, data_dict: typing.Optional[typing.Dict] = None
) -> typing.Dict:
    """
    Check whether current user can resign from role of NSIF moderator of a DCPR request
    """

    request_id = toolkit.get_or_bust(data_dict, "csi_reference_id")
    request_obj = dcpr_request.DCPRRequest.get(request_id)
    result = {"success": False}
    if request_obj is not None:
        if request_obj.status == DCPRRequestStatus.UNDER_NSIF_REVIEW.value:
            # TODO: Should probably also check for sysadmins, as they ought to be
            #  allowed to perform this action
            is_current_nsif_reviewer = (
                request_obj.nsif_reviewer == context["auth_user_obj"].id
            )
            if is_current_nsif_reviewer:
                result["success"] = True
            else:
                result["msg"] = toolkit._(
                    "Current user is not authorized to resign from the role of NSIF "
                    "moderator for this DCPR request"
                )
        else:
            result["msg"] = toolkit._("DCPR request is not currently under NSIF review")
    else:
        result["msg"] = toolkit._("Request not found")
    return result


def dcpr_request_resign_csi_reviewer_auth(
    context: typing.Dict, data_dict: typing.Optional[typing.Dict] = None
) -> typing.Dict:
    """
    Check whether current user can resign from role of CSI moderator of a DCPR request
    """

    request_id = toolkit.get_or_bust(data_dict, "csi_reference_id")
    request_obj = dcpr_request.DCPRRequest.get(request_id)
    result = {"success": False}
    if request_obj is not None:
        if request_obj.status == DCPRRequestStatus.UNDER_CSI_REVIEW.value:
            # TODO: Should probably also check for sysadmins, as they ought to be
            #  allowed to perform this action
            is_current_csi_moderator = (
                request_obj.csi_moderator == context["auth_user_obj"].id
            )
            if is_current_csi_moderator:
                result["success"] = True
            else:
                result["msg"] = toolkit._(
                    "Current user is not authorized to resign from the role of CSI "
                    "moderator for this DCPR request"
                )
        else:
            result["msg"] = toolkit._("DCPR request is not currently under CSI review")
    else:
        result["msg"] = toolkit._("Request not found")
    return result
