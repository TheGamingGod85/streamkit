from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from api.services.supabase_client import SupabaseRepository

router = APIRouter(tags=["compatibility"])


@router.get("/ik/{workspace_id}/{path:path}")
async def imagekit_rewriter(request: Request, workspace_id: str, path: str):
    tr = request.query_params.get("tr", "")

    if path.startswith("tr:"):
        parts = path.split("/", 1)
        if len(parts) == 2:
            tr = parts[0].replace("tr:", "")
            path = parts[1]

    params = {}
    if tr:
        for part in tr.split(","):
            if "-" in part:
                k, v = part.split("-", 1)
                if k == "w":
                    params["w"] = v
                elif k == "h":
                    params["h"] = v
                elif k == "f":
                    params["format"] = v
                elif k == "q":
                    params["q"] = v
                elif k == "c" and v == "maintain_ratio":
                    params["fit"] = "contain"
                elif k == "c" and v == "force":
                    params["fit"] = "cover"
                elif k == "bl":
                    params["blur"] = v

    settings = request.app.state.settings
    supabase = await SupabaseRepository.create(settings)
    res = await supabase.client.table("origins").select("id").eq("workspace_id", workspace_id).limit(1).execute()
    await supabase.aclose()

    origin_id = res.data[0]["id"] if res.data else None

    if origin_id:
        redirect_url = f"/proxy/{origin_id}/{path}"
    else:
        redirect_url = f"/media/{path}"

    if params:
        redirect_url += f"?{urlencode(params)}"

    return RedirectResponse(url=redirect_url)


@router.get("/cloudinary/{cloud_name}/image/upload/{transformations}/{path:path}")
async def cloudinary_rewriter(cloud_name: str, transformations: str, path: str):
    params = {}
    if transformations:
        for part in transformations.split(","):
            if part.startswith("w_"):
                params["w"] = part[2:]
            elif part.startswith("h_"):
                params["h"] = part[2:]
            elif part.startswith("c_fit"):
                params["fit"] = "contain"
            elif part.startswith("c_fill"):
                params["fit"] = "cover"
            elif part.startswith("e_blur:"):
                params["blur"] = part.split(":", 1)[1]
            elif part.startswith("q_"):
                params["q"] = part[2:]
            elif part.startswith("f_"):
                params["format"] = part[2:]

    redirect_url = f"/ik/{cloud_name}/{path}"
    if params:
        redirect_url += f"?{urlencode(params)}"
    return RedirectResponse(url=redirect_url)
