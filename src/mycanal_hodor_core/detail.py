"""Read verified detail fields and request descriptors."""

def extract_subgenre(payload: dict) -> str:
    detail = payload.get('detail')
    if isinstance(detail, dict):
        value = detail.get('subgenre')
        if isinstance(value, str) and value.strip():
            return value
    tracking = payload.get('tracking')
    if not isinstance(tracking, dict):
        return ''
    data_layer = tracking.get('dataLayer')
    if not isinstance(data_layer, dict):
        return ''
    value = data_layer.get('subgenre')
    return value if isinstance(value, str) and value.strip() else ''


def extract_duration(payload: dict) -> int | None:
    detail = payload.get('detail')
    # Only the observed movie schema is supported; never aggregate series/episodes.
    if not isinstance(detail, dict) or detail.get('genre') != 'Cinéma':
        return None
    minutes = detail.get('duration')
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0:
        return None
    return minutes


def declares_detail_v5(parameters: object) -> bool:
    if not isinstance(parameters, list):
        return False
    for parameter in parameters:
        if not isinstance(parameter, dict):
            continue
        values = parameter.get('enum')
        if (parameter.get('in') == 'parameters'
                and parameter.get('id') == 'featureToggles'
                and isinstance(values, list)
                and all(isinstance(value, str) for value in values)
                and 'detailV5' in values):
            return True
    return False


