"""
Stripe billing and subscription management.
"""
import logging
import stripe
from fastapi import HTTPException

from models import (
    CreateCheckoutSessionResponse,
    SubscriptionStatusResponse,
    CancelSubscriptionResponse
)

logger = logging.getLogger(__name__)

# Constants will be imported from app.py or constants.py
STRIPE_SECRET_KEY = None
STRIPE_PRICE_ID = None


def set_stripe_config(secret_key: str, price_id: str):
    """Initialize Stripe configuration"""
    global STRIPE_SECRET_KEY, STRIPE_PRICE_ID
    STRIPE_SECRET_KEY = secret_key
    STRIPE_PRICE_ID = price_id
    stripe.api_key = secret_key


async def create_stripe_checkout_session(
    user_id: str,
    email: str,
    success_url: str,
    cancel_url: str
) -> CreateCheckoutSessionResponse:
    """Create a Stripe checkout session for subscription

    Args:
        user_id: User ID from auth token
        email: User email
        success_url: URL to redirect to on success
        cancel_url: URL to redirect to on cancellation

    Returns:
        CreateCheckoutSessionResponse with checkout_url and session_id

    Raises:
        HTTPException: If checkout session creation fails
    """
    try:
        # Create Stripe customer if doesn't exist
        customers = stripe.Customer.list(email=email, limit=1)
        if customers.data:
            customer = customers.data[0]
        else:
            customer = stripe.Customer.create(
                email=email,
                metadata={"user_id": user_id}
            )

        # Create checkout session
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price': STRIPE_PRICE_ID,
                'quantity': 1,
            }],
            mode='subscription',
            success_url=success_url,
            cancel_url=cancel_url,
            customer=customer.id,
            metadata={"user_id": user_id}
        )

        return CreateCheckoutSessionResponse(
            checkout_url=checkout_session.url,
            session_id=checkout_session.id
        )

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error creating checkout session: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating checkout session: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create checkout session")


async def get_user_subscription_status(
    user_id: str,
    email: str
) -> SubscriptionStatusResponse:
    """Get current subscription status for the user

    Args:
        user_id: User ID from auth token
        email: User email

    Returns:
        SubscriptionStatusResponse with subscription details

    Raises:
        HTTPException: If status retrieval fails
    """
    try:
        # Find customer by email
        customers = stripe.Customer.list(email=email, limit=1)
        if not customers.data:
            return SubscriptionStatusResponse(has_subscription=False)

        customer = customers.data[0]

        # Get active subscriptions
        subscriptions = stripe.Subscription.list(
            customer=customer.id,
            status='active',
            limit=1
        )

        if not subscriptions.data:
            # Check for canceled subscriptions that are still active until period end
            subscriptions = stripe.Subscription.list(
                customer=customer.id,
                status='all',
                limit=1
            )
            if subscriptions.data and subscriptions.data[0].status in ['active', 'trialing']:
                subscription = subscriptions.data[0]
                return SubscriptionStatusResponse(
                    has_subscription=True,
                    subscription_id=subscription.id,
                    status=subscription.status,
                    current_period_end=subscription.current_period_end,
                    cancel_at_period_end=subscription.cancel_at_period_end
                )
            return SubscriptionStatusResponse(has_subscription=False)

        subscription = subscriptions.data[0]
        return SubscriptionStatusResponse(
            has_subscription=True,
            subscription_id=subscription.id,
            status=subscription.status,
            current_period_end=subscription.current_period_end,
            cancel_at_period_end=subscription.cancel_at_period_end
        )

    except Exception as e:
        logger.error(f"Error getting subscription status: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get subscription status")


async def cancel_user_subscription(
    user_id: str,
    email: str
) -> CancelSubscriptionResponse:
    """Cancel the user's subscription at period end

    Args:
        user_id: User ID from auth token
        email: User email

    Returns:
        CancelSubscriptionResponse with cancellation details

    Raises:
        HTTPException: If cancellation fails
    """
    try:
        # Find customer by email
        customers = stripe.Customer.list(email=email, limit=1)
        if not customers.data:
            raise HTTPException(status_code=404, detail="No subscription found")

        customer = customers.data[0]

        # Get active subscriptions
        subscriptions = stripe.Subscription.list(
            customer=customer.id,
            status='active',
            limit=1
        )

        if not subscriptions.data:
            raise HTTPException(status_code=404, detail="No active subscription found")

        # Cancel subscription at period end
        subscription = stripe.Subscription.modify(
            subscriptions.data[0].id,
            cancel_at_period_end=True
        )

        return CancelSubscriptionResponse(
            status="success",
            message="Subscription will be canceled at the end of the billing period",
            canceled_at=subscription.current_period_end
        )

    except stripe.error.StripeError as e:
        logger.error(f"Stripe error canceling subscription: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error canceling subscription: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to cancel subscription")
