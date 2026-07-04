

def append_org_sy_prompt(org:str,prompt:str)->str:
    """
    Append the github organization details to the system prompt.
    """
    return f"## Gitub organization = {org}\n\n{prompt}"