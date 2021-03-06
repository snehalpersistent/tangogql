"""Module containing the different types."""

import math

from graphene import ObjectType, String
from graphene.types import Scalar

from graphql.language import ast


class TangoNodeType(ObjectType):
    """This class represents type of a node in Tango."""

    nodetype = String()

    def resolve_nodetype(self, info):
        """This method gets the type of the node in Tango.

        :return: Name of the type.
        :rtype: str
        """

        return type(self).__name__.lower()


class ScalarTypes(Scalar):
    """
    This class makes it possible to have input and output of different types.

    The ScalarTypes represents a generic scalar value that could be:
    Int, String, Boolean, Float, List.
    """

    @staticmethod
    def coerce_type(value):
        """This method just return the input value.

        :param value: Any

        :return: Value (any)
        """

        # value of type DevState should return as string
        if type(value).__name__ == "DevState":
            return str(value)
        # json don't have support on infinity
        elif isinstance(value, float):
            if math.isinf(value):
                return str(value)
        return value

    # TODO: Check if the following static methods really need to be static.
    @staticmethod
    def serialize(value):
        return ScalarTypes.coerce_type(value)

    @staticmethod
    def parse_value(value):
        """This method is called when an assignment is made.

        :param value: value(any)

        :return: value(any)
        """

        return ScalarTypes.coerce_type(value)

    # Called for the input
    @staticmethod
    def parse_literal(node):
        """This method is called when the value of type *ScalarTypes* is used
        as input.

        :param node: value(any)

        :return: Return an exception when it is not possible to parse the value
                 to one of the scalar types.
        :rtype: bool, str, int, float or Exception
        """

        try:
            if isinstance(node, ast.IntValue):
                return int(node.value)
            elif isinstance(node, ast.FloatValue):
                return float(node.value)
            elif isinstance(node, ast.BooleanValue):
                return node.value
            elif isinstance(node, ast.ListValue):
                return [ScalarTypes.parse_literal(value)
                        for value in node.values]
            elif isinstance(node, ast.StringValue):
                return str(node.value)
            else:
                raise ValueError('The input value is not of acceptable types')
        except Exception as e:
            return e
