from typing import TYPE_CHECKING

import aio_pika

if TYPE_CHECKING:
    from vcs_gateway.config import Settings


async def create_amqp_connection(settings: "Settings") -> aio_pika.RobustConnection:
    """
    Create a RobustConnection that handles reconnects automatically.
    Store the result on app.state.amqp_connection.
    """
    return await aio_pika.connect_robust(str(settings.rabbitmq_url))
