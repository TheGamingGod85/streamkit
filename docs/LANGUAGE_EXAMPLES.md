# StreamKit Language Examples

This guide provides copy-paste integration snippets in multiple programming languages.

Languages covered:

- curl
- JavaScript / TypeScript
- Python
- Go
- Java
- C#

## Shared Setup

Set these variables in your environment:

- STREAMKIT_BASE_URL (example: http://localhost:8000)
- STREAMKIT_TOKEN (Supabase access token for protected endpoints)
- WORKSPACE_ID

## 1) Create Workspace (Authenticated)

## curl

```bash
curl -X POST "$STREAMKIT_BASE_URL/me/workspaces" \
  -H "Authorization: Bearer $STREAMKIT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"workspace_id":"my-workspace","name":"My Workspace"}'
```

## JavaScript / TypeScript (fetch)

```ts
const res = await fetch(`${process.env.STREAMKIT_BASE_URL}/me/workspaces`, {
  method: "POST",
  headers: {
    "Authorization": `Bearer ${process.env.STREAMKIT_TOKEN}`,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({ workspace_id: "my-workspace", name: "My Workspace" }),
});
const data = await res.json();
console.log(data);
```

## Python (requests)

```python
import os
import requests

base = os.environ["STREAMKIT_BASE_URL"]
token = os.environ["STREAMKIT_TOKEN"]

resp = requests.post(
    f"{base}/me/workspaces",
    headers={"Authorization": f"Bearer {token}"},
    json={"workspace_id": "my-workspace", "name": "My Workspace"},
    timeout=30,
)
print(resp.status_code, resp.json())
```

## Go

```go
package main

import (
	"bytes"
	"fmt"
	"io"
	"net/http"
	"os"
)

func main() {
	base := os.Getenv("STREAMKIT_BASE_URL")
	token := os.Getenv("STREAMKIT_TOKEN")
	body := []byte(`{"workspace_id":"my-workspace","name":"My Workspace"}`)

	req, _ := http.NewRequest("POST", base+"/me/workspaces", bytes.NewBuffer(body))
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		panic(err)
	}
	defer resp.Body.Close()
	out, _ := io.ReadAll(resp.Body)
	fmt.Println(resp.StatusCode, string(out))
}
```

## Java (HttpClient)

```java
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;

public class CreateWorkspace {
  public static void main(String[] args) throws Exception {
    String base = System.getenv("STREAMKIT_BASE_URL");
    String token = System.getenv("STREAMKIT_TOKEN");
    String json = "{\"workspace_id\":\"my-workspace\",\"name\":\"My Workspace\"}";

    HttpRequest req = HttpRequest.newBuilder()
      .uri(URI.create(base + "/me/workspaces"))
      .header("Authorization", "Bearer " + token)
      .header("Content-Type", "application/json")
      .POST(HttpRequest.BodyPublishers.ofString(json))
      .build();

    HttpResponse<String> res = HttpClient.newHttpClient().send(req, HttpResponse.BodyHandlers.ofString());
    System.out.println(res.statusCode());
    System.out.println(res.body());
  }
}
```

## C# (.NET HttpClient)

```csharp
using System.Net.Http.Headers;
using System.Text;

var baseUrl = Environment.GetEnvironmentVariable("STREAMKIT_BASE_URL")!;
var token = Environment.GetEnvironmentVariable("STREAMKIT_TOKEN")!;

using var client = new HttpClient();
client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);

var payload = "{\"workspace_id\":\"my-workspace\",\"name\":\"My Workspace\"}";
var content = new StringContent(payload, Encoding.UTF8, "application/json");
var response = await client.PostAsync($"{baseUrl}/me/workspaces", content);

Console.WriteLine((int)response.StatusCode);
Console.WriteLine(await response.Content.ReadAsStringAsync());
```

## 2) Upload File

## curl

```bash
curl -X POST "$STREAMKIT_BASE_URL/upload" \
  -H "Authorization: Bearer $STREAMKIT_TOKEN" \
  -F "workspace_id=$WORKSPACE_ID" \
  -F "file=@./image.jpg"
```

## JavaScript / TypeScript

```ts
const form = new FormData();
form.append("workspace_id", process.env.WORKSPACE_ID!);
form.append("file", fileInput.files![0]);

const res = await fetch(`${process.env.STREAMKIT_BASE_URL}/upload`, {
  method: "POST",
  headers: { "Authorization": `Bearer ${process.env.STREAMKIT_TOKEN}` },
  body: form,
});
const data = await res.json();
console.log(data.data.asset_id, data.data.status_url);
```

## Python

```python
import os
import requests

base = os.environ["STREAMKIT_BASE_URL"]
token = os.environ["STREAMKIT_TOKEN"]
workspace_id = os.environ["WORKSPACE_ID"]

with open("image.jpg", "rb") as f:
    resp = requests.post(
        f"{base}/upload",
        headers={"Authorization": f"Bearer {token}"},
        data={"workspace_id": workspace_id},
        files={"file": ("image.jpg", f, "image/jpeg")},
        timeout=120,
    )
print(resp.status_code, resp.json())
```

## 3) Check Asset Status

## curl

```bash
curl "$STREAMKIT_BASE_URL/status/$ASSET_ID" \
  -H "Authorization: Bearer $STREAMKIT_TOKEN"
```

## JavaScript / TypeScript

```ts
const res = await fetch(`${process.env.STREAMKIT_BASE_URL}/status/${assetId}`, {
  headers: { "Authorization": `Bearer ${process.env.STREAMKIT_TOKEN}` }
});
console.log(await res.json());
```

## 4) Transform and Deliver

## URL pattern

```text
GET /media/{asset_id}?w=800&h=600&fit=cover&f=webp&q=85
```

## curl

```bash
curl "$STREAMKIT_BASE_URL/media/$ASSET_ID?w=800&h=600&f=webp&q=85" -o out.webp
```

## JavaScript / TypeScript

```ts
const url = `${process.env.STREAMKIT_BASE_URL}/media/${assetId}?w=800&h=600&f=webp&q=85`;
const blob = await (await fetch(url)).blob();
```

## 5) Origins CRUD (Create, Update, Delete)

## Create origin (curl)

```bash
curl -X POST "$STREAMKIT_BASE_URL/origins" \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id":"'$WORKSPACE_ID'",
    "name":"Main Bucket",
    "type":"s3",
    "config":{
      "bucket":"my-bucket",
      "bucket_folder":"marketing",
      "access_key":"AKIA...",
      "secret_key":"...",
      "region":"us-east-1"
    }
  }'
```

## Update origin endpoint fields (curl)

```bash
curl -X PUT "$STREAMKIT_BASE_URL/origins/$ORIGIN_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "name":"Main Bucket Updated",
    "config":{
      "bucket":"my-bucket",
      "bucket_folder":"campaigns/2026",
      "endpoint":"https://s3.us-east-1.amazonaws.com",
      "region":"us-east-1",
      "access_key":"AKIA...",
      "secret_key":"..."
    }
  }'
```

## Delete origin (curl)

```bash
curl -X DELETE "$STREAMKIT_BASE_URL/origins/$ORIGIN_ID"
```

## Update origin (JavaScript / TypeScript)

```ts
await fetch(`${process.env.STREAMKIT_BASE_URL}/origins/${originId}`, {
  method: "PUT",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    name: "Main Bucket Updated",
    config: {
      bucket: "my-bucket",
      bucket_folder: "campaigns/2026",
      endpoint: "https://s3.us-east-1.amazonaws.com",
      region: "us-east-1",
      access_key: process.env.S3_ACCESS_KEY,
      secret_key: process.env.S3_SECRET_KEY,
    },
  }),
});
```

## Update origin (Python)

```python
import os
import requests

base = os.environ["STREAMKIT_BASE_URL"]
origin_id = os.environ["ORIGIN_ID"]

resp = requests.put(
    f"{base}/origins/{origin_id}",
    json={
        "name": "Main Bucket Updated",
        "config": {
            "bucket": "my-bucket",
            "bucket_folder": "campaigns/2026",
            "endpoint": "https://s3.us-east-1.amazonaws.com",
            "region": "us-east-1",
            "access_key": os.environ["S3_ACCESS_KEY"],
            "secret_key": os.environ["S3_SECRET_KEY"],
        },
    },
    timeout=30,
)
print(resp.status_code, resp.json())
```

## 6) Origin Proxy Delivery

## URL pattern

```text
GET /proxy/{origin_id}/{path}?w=1200&f=webp&q=80
```

## curl

```bash
curl "$STREAMKIT_BASE_URL/proxy/$ORIGIN_ID/products/shoe-1.jpg?w=1200&f=webp&q=80" -o shoe.webp
```

## 7) Analytics Event

## curl

```bash
curl -X POST "$STREAMKIT_BASE_URL/analytics/events" \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id":"'$WORKSPACE_ID'",
    "event_type":"media.request",
    "metadata":{"sdk":"custom-client"}
  }'
```

## Tips for Client Library Authors

- Wrap HTTP calls in typed methods per endpoint group.
- Centralize auth header injection.
- Add automatic retries for 502 and transient network failures.
- For uploads, expose progress callbacks.
- For transforms, model query params as a typed options object.
- Log response headers like X-Cache, X-Image-Format for diagnostics.
