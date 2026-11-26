from rest_framework import status
from rest_framework.exceptions import APIException


class ConflictError(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = 'Operacja jest sprzeczna z bieżącym stanem zasobu.'
    default_code = 'conflict'
