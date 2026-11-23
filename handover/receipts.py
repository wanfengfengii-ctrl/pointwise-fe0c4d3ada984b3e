from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FirstReceipt:
    receipt_id: int
    capture_id: int


def create_first_receipt(receipt_id: int, capture_id: int) -> FirstReceipt:
    if type(receipt_id) is not int or receipt_id <= 0:
        raise ValueError('invalid first receipt number')
    if type(capture_id) is not int or capture_id <= 0:
        raise ValueError('invalid receipt capture number')
    return FirstReceipt(receipt_id, capture_id)
