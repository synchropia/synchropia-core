from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

# Import necessary components
from database import get_db, Item

router = APIRouter()

# --- Mock Authentication Dependency ---
# In a real application, this would validate a JWT token against an Auth service.
def get_current_user(token: str = Depends(lambda: None)):
    """
    Dependency to mock token validation.
    In a real scenario, this would decode the token and verify user identity.
    For this simple setup, we just check if a token is present (or mock a successful user).
    """
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials not provided or invalid token."
        )
    # Mock successful user return
    return {"user_id": "mock_user_123", "role": "developer"}


@router.get("/items", response_model=List[dict], tags=["Items"])
def read_items(
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user) # Dependency Injection for Auth
):
    """
    Retrieves all items from the database, secured by token authentication.
    """
    try:
        items = db.query(Item).all()
        
        # Format the output to be clean
        item_list = []
        for item in items:
            item_list.append({
                "id": item.id,
                "name": item.name,
                "description": item.description,
                "created_at": item.created_at.isoformat()
            })
        
        return item_list
    except Exception as e:
        # Log the database error internally but return a generic 500 to the client
        print(f"Database error during item retrieval: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred while fetching items."
        )

if __name__ == "__main__":
    get_current_user()
    read_items()
