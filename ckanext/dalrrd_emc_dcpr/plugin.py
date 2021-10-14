import logging
import typing

import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit
from ckan.logic.validators import (
    boolean_validator,
    datasets_with_no_organization_cannot_be_private,
)

from .commands.test import test_ckan_cmd

logger = logging.getLogger(__name__)


class DalrrdEmcDcprPlugin(plugins.SingletonPlugin, toolkit.DefaultDatasetForm):
    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IClick)
    plugins.implements(plugins.IDatasetForm)
    plugins.implements(plugins.IAuthFunctions)
    plugins.implements(plugins.IActions)

    def update_config(self, config_):
        toolkit.add_template_directory(config_, "templates")
        toolkit.add_public_directory(config_, "public")
        toolkit.add_resource("fanstatic", "dalrrd_emc_dcpr")

    def get_commands(self):
        return [test_ckan_cmd]

    # IDatasetForm

    def _admins_only_create(self, key, data, errors, context):
        user_name = context.get("user")
        private = data[key]

        owner_org_list = [value for key, value in data.items() if key[0] == "owner_org"]
        owner_org = owner_org_list[0] if owner_org_list else None

        members = toolkit.get_action("member_list")(
            data_dict={"id": owner_org, "object_type": "user"}
        )

        admin_member_ids = [
            member_tuple[0] for member_tuple in members if member_tuple[2] == "Admin"
        ]

        convert_user_name_or_id_to_id = toolkit.get_converter(
            "convert_user_name_or_id_to_id"
        )
        user_id = convert_user_name_or_id_to_id(user_name, context)

        if (not private) and (user_id not in admin_member_ids):
            raise toolkit.Invalid("Only Admin users may set datasets as public")
        else:
            return private

    def _admins_only_update(self, value, context):

        package = context.get("package")
        user_name = context.get("user")
        private = value

        members = toolkit.get_action("member_list")(
            data_dict={"id": package.owner_org, "object_type": "user"}
        )

        admin_member_ids = [
            member_tuple[0] for member_tuple in members if member_tuple[2] == "Admin"
        ]

        convert_user_name_or_id_to_id = toolkit.get_converter(
            "convert_user_name_or_id_to_id"
        )
        user_id = convert_user_name_or_id_to_id(user_name, context)
        if (not private) and (user_id not in admin_member_ids):
            raise toolkit.Invalid(
                "Only Admin users may set datasets as public " "or edit public datasets"
            )
        else:
            return value

    def create_package_schema(self):
        schema = super(DalrrdEmcDcprPlugin, self).create_package_schema()

        create_package_validators = [
            boolean_validator,
            datasets_with_no_organization_cannot_be_private,
            self._admins_only_create,
        ]

        schema.update({"private": create_package_validators})
        return schema

    def update_package_schema(self):
        schema = super(DalrrdEmcDcprPlugin, self).update_package_schema()
        logger.debug(f"schema: {schema}")

        update_package_validators = [
            boolean_validator,
            datasets_with_no_organization_cannot_be_private,
            self._admins_only_update,
        ]

        schema.update({"private": update_package_validators})
        return schema

    def is_fallback(self):
        return True

    def package_types(self):
        return []

    def get_auth_functions(self) -> typing.Dict[str, typing.Callable]:
        return {
            "package_publish": authorize_package_publish,
        }

    def get_actions(self) -> typing.Dict[str, typing.Callable]:
        return {
            "package_update": package_update,
        }


def authorize_package_publish(
    next_auth, context: typing.Dict, data_dict: typing.Optional[typing.Dict] = None
) -> typing.Dict[str, bool]:
    result = False
    logger.debug(f"Inside authorize_package_publish function: {locals()}")
    return {"success": result, "msg": "You are not authorized to publish a package"}


@toolkit.chained_action
def package_update(original_action, context, data_dict):
    """
    Intercepts the core `package_update` action to check if package is being published.
    """
    remains_private = toolkit.asbool(data_dict.get("private", False))
    result = None
    if remains_private:
        result = original_action(context, data_dict)
    else:
        toolkit.check_access("package_publish", context, data_dict)
    return result
