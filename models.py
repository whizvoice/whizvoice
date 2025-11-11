"""
Pydantic models for API request/response schemas.
"""
from pydantic import BaseModel
from typing import Optional, Dict, Any, List


# Chat models
class ChatMessage(BaseModel):
    content: str
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    content: str
    session_id: str


# Authentication models
class GoogleTokenRequest(BaseModel):
    token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    user: Dict[str, Any]


class TestAuthRequest(BaseModel):
    email: str
    user_id: str
    name: Optional[str] = "Test User"


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class NewAccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# API key and token models
class UserApiKeySetRequest(BaseModel):
    key_name: str
    key_value: Optional[str]  # Allow None to potentially clear a key


class TokenUpdateRequest(BaseModel):
    claude_api_key: Optional[str] = None
    asana_access_token: Optional[str] = None


class ApiTokenStatusResponse(BaseModel):
    has_claude_token: bool
    has_asana_token: bool


# User preference models
class SetTimezoneRequest(BaseModel):
    timezone: str


# Conversation and Message API models
class ConversationCreate(BaseModel):
    title: str
    source: str = "app"
    google_session_id: Optional[str] = None


class ConversationUpdate(BaseModel):
    title: Optional[str] = None


class ConversationResponse(BaseModel):
    id: int
    user_id: str
    title: str
    created_at: str
    last_message_time: str
    source: str
    google_session_id: Optional[str] = None
    deleted_at: Optional[str] = None


class MessageCreate(BaseModel):
    conversation_id: int
    content: str
    message_type: str  # 'USER' or 'ASSISTANT'
    request_id: Optional[str] = None  # Client-generated UUID for request tracking
    timestamp: Optional[str] = None  # Optional ISO format timestamp for preserving message order


class MessageResponse(BaseModel):
    id: int
    conversation_id: int
    content: str
    message_type: str
    timestamp: str
    request_id: Optional[str] = None  # Request ID for tracking request/response pairs


# Dialogflow webhook models
class DialogflowWebhookRequest(BaseModel):
    detectIntentResponseId: Optional[str] = None
    pageInfo: Optional[Dict[str, Any]] = None
    sessionInfo: Optional[Dict[str, Any]] = None
    fulfillmentInfo: Optional[Dict[str, Any]] = None
    messages: Optional[List[Dict[str, Any]]] = None
    payload: Optional[Dict[str, Any]] = None
    sentimentAnalysisResult: Optional[Dict[str, Any]] = None
    text: Optional[str] = None
    triggerIntent: Optional[str] = None
    triggerEvent: Optional[str] = None


class DialogflowWebhookResponse(BaseModel):
    fulfillmentResponse: Optional[Dict[str, Any]] = None
    pageInfo: Optional[Dict[str, Any]] = None
    sessionInfo: Optional[Dict[str, Any]] = None
    payload: Optional[Dict[str, Any]] = None


# Billing/Subscription models
class CreateCheckoutSessionRequest(BaseModel):
    success_url: str
    cancel_url: str


class CreateCheckoutSessionResponse(BaseModel):
    checkout_url: str
    session_id: str


class CancelSubscriptionResponse(BaseModel):
    status: str
    message: str
    canceled_at: Optional[int] = None


class SubscriptionStatusResponse(BaseModel):
    has_subscription: bool
    subscription_id: Optional[str] = None
    status: Optional[str] = None
    current_period_end: Optional[int] = None
    cancel_at_period_end: Optional[bool] = None
