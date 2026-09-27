param(
    [string]$EnvFile = "",
    [string]$Query = "OpenAI official website"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Read-DotEnv {
    param([string]$Path)

    $values = @{}
    foreach ($line in [IO.File]::ReadAllLines($Path)) {
        $trimmed = $line.Trim()
        if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) {
            continue
        }

        $separator = $trimmed.IndexOf("=")
        if ($separator -lt 1) {
            throw "Invalid .env line: expected NAME=VALUE"
        }

        $name = $trimmed.Substring(0, $separator).Trim()
        if ($name -notmatch "^[A-Za-z_][A-Za-z0-9_]*$") {
            throw "Invalid environment variable name in .env: $name"
        }

        $value = $trimmed.Substring($separator + 1).Trim()
        if ($value.Length -ge 2) {
            $first = $value.Substring(0, 1)
            $last = $value.Substring($value.Length - 1, 1)
            if (($first -eq "'" -and $last -eq "'") -or ($first -eq '"' -and $last -eq '"')) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        $values[$name] = $value
    }
    return $values
}

function Get-RequiredValue {
    param(
        [hashtable]$Values,
        [string[]]$Names
    )

    foreach ($name in $Names) {
        if ($Values.ContainsKey($name) -and -not [string]::IsNullOrWhiteSpace($Values[$name])) {
            return $Values[$name]
        }
    }
    throw "Missing required value in .env: $($Names -join ' or ')"
}

function Invoke-Docker {
    param([string[]]$Arguments)

    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed: docker $($Arguments -join ' ')"
    }
}

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
if ([string]::IsNullOrWhiteSpace($EnvFile)) {
    $EnvFile = Join-Path $repositoryRoot "mcp/domain-research/server/.env"
}
$EnvFile = [IO.Path]::GetFullPath($EnvFile)
if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    throw ".env file not found: $EnvFile"
}
if ([string]::IsNullOrWhiteSpace($Query)) {
    throw "Query must not be blank"
}

Get-Command docker -ErrorAction Stop | Out-Null
$dotenv = Read-DotEnv -Path $EnvFile
$requiredEnvironment = @{
    HIDDIFY_SUBSCRIPTION_URL = Get-RequiredValue $dotenv @("HIDDIFY_SUBSCRIPTION_URL")
    OIL_MCP_TAVILY_API_KEY = Get-RequiredValue $dotenv @("OIL_MCP_TAVILY_API_KEY", "TAVILY_API_KEY")
    OIL_MCP_BUNDLE_RECEIPT_KEY = Get-RequiredValue $dotenv @("OIL_MCP_BUNDLE_RECEIPT_KEY", "BUNDLE_RECEIPT_KEY")
    OIL_MCP_OBJECT_STORAGE_ALLOWED_ORIGINS = Get-RequiredValue $dotenv @("OIL_MCP_OBJECT_STORAGE_ALLOWED_ORIGINS", "OBJECT_STORAGE_ALLOWED_ORIGINS")
}

$runtimeDirectory = Join-Path ([IO.Path]::GetTempPath()) ("oil-mcp-smoke-" + [guid]::NewGuid().ToString("N"))
$composeFile = Join-Path $repositoryRoot "deploy/oil-mcp/docker-compose.yml"
$projectName = "oil-mcp-smoke"
$hiddifyVolumeName = "oil-mcp-smoke_hiddify_state"
$environmentNames = @(
    "HIDDIFY_SUBSCRIPTION_URL",
    "OIL_MCP_TAVILY_API_KEY",
    "OIL_MCP_BUNDLE_RECEIPT_KEY",
    "OIL_MCP_OBJECT_STORAGE_ALLOWED_ORIGINS",
    "OIL_MCP_SOURCE",
    "OIL_MCP_RUNTIME_ENV_DIR",
    "OIL_MCP_HIDDIFY_VOLUME_NAME",
    "OIL_MCP_DOMAIN_RESEARCH_PORT"
)
$previousEnvironment = @{}
foreach ($name in $environmentNames) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}

try {
    New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
    foreach ($name in $requiredEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $requiredEnvironment[$name], "Process")
    }
    $env:OIL_MCP_SOURCE = $repositoryRoot
    $env:OIL_MCP_RUNTIME_ENV_DIR = $runtimeDirectory
    $env:OIL_MCP_HIDDIFY_VOLUME_NAME = $hiddifyVolumeName
    $env:OIL_MCP_DOMAIN_RESEARCH_PORT = "18023"

    Invoke-Docker @("version", "--format", "{{.Server.Version}}")

    Invoke-Docker @(
        "run", "--rm",
        "--env", "HIDDIFY_SUBSCRIPTION_URL",
        "--env", "OIL_MCP_TAVILY_API_KEY",
        "--env", "OIL_MCP_BUNDLE_RECEIPT_KEY",
        "--env", "OIL_MCP_OBJECT_STORAGE_ALLOWED_ORIGINS",
        "--mount", "type=bind,source=$repositoryRoot,target=/repo,readonly",
        "--mount", "type=bind,source=$runtimeDirectory,target=/runtime",
        "bash:5.2",
        "bash", "/repo/deploy/oil-mcp/render-runtime-env.sh", "/runtime", "domain-research"
    )

    Invoke-Docker @(
        "compose", "--project-name", $projectName, "-f", $composeFile,
        "config", "--quiet"
    )
    Invoke-Docker @(
        "compose", "--project-name", $projectName, "-f", $composeFile,
        "build", "hiddify-proxy"
    )
    Invoke-Docker @(
        "compose", "--project-name", $projectName, "-f", $composeFile,
        "create", "--force-recreate", "hiddify-proxy"
    )
    $proxyContainerArguments = @(
        "compose", "--project-name", $projectName, "-f", $composeFile,
        "ps", "-aq", "hiddify-proxy"
    )
    $proxyContainer = (& docker @proxyContainerArguments | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($proxyContainer)) {
        throw "Could not resolve the Hiddify proxy container"
    }
    Invoke-Docker @(
        "cp", (Join-Path $runtimeDirectory "hiddify/subscription-url"),
        "${proxyContainer}:/var/lib/hiddify/subscription-url"
    )
    Invoke-Docker @(
        "compose", "--project-name", $projectName, "-f", $composeFile,
        "up", "-d", "--build", "--force-recreate", "--remove-orphans", "--wait", "--wait-timeout", "300",
        "hiddify-proxy", "domain-research-mcp"
    )

    $proxyProbeArguments = @(
        "compose", "--project-name", $projectName, "-f", $composeFile,
        "exec", "-T", "domain-research-mcp", "python", "-c",
        "import httpx; response=httpx.get('https://www.gstatic.com/generate_204', timeout=15.0); response.raise_for_status(); assert response.status_code == 204"
    )
    foreach ($phase in @("initial", "restart")) {
        $proxyReady = $false
        for ($attempt = 1; $attempt -le 12; $attempt++) {
            $probeErrorActionPreference = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            try {
                & docker @proxyProbeArguments 2>$null
                $probeExitCode = $LASTEXITCODE
            }
            finally {
                $ErrorActionPreference = $probeErrorActionPreference
            }
            if ($probeExitCode -eq 0) {
                $proxyReady = $true
                break
            }
            Start-Sleep -Seconds 5
        }
        if (-not $proxyReady) {
            throw "Hiddify proxy did not become ready after 12 attempts"
        }
        if ($phase -eq "restart") {
            break
        }

        Invoke-Docker @(
            "compose", "--project-name", $projectName, "-f", $composeFile,
            "stop", "hiddify-proxy"
        )
        $probeErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & docker @proxyProbeArguments 2>$null
            $probeExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $probeErrorActionPreference
        }
        if ($probeExitCode -eq 0) {
            throw "Domain Research retained direct internet access after Hiddify stopped"
        }
        Invoke-Docker @(
            "compose", "--project-name", $projectName, "-f", $composeFile,
            "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "300",
            "hiddify-proxy"
        )
    }

    $clientProgram = @'
import asyncio
import json
import os
from fastmcp import Client

async def main():
    async with Client("http://127.0.0.1:8003/mcp") as client:
        result = await client.call_tool(
            "search_web",
            {"query": os.environ["OIL_MCP_SMOKE_QUERY"], "max_results": 3},
        )
    payload = result.structured_content or {}
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    if payload.get("status") != "ok":
        raise SystemExit(1)

asyncio.run(main())
'@
    $callArguments = @(
        "compose", "--project-name", $projectName, "-f", $composeFile,
        "exec", "-T", "-e", "OIL_MCP_SMOKE_QUERY=$Query",
        "domain-research-mcp", "python", "-"
    )
    $callOutput = $clientProgram | & docker @callArguments 2>&1
    $callExitCode = $LASTEXITCODE
    $callOutput | Write-Output
    if ($callExitCode -ne 0) {
        throw "Domain Research MCP search_web call failed"
    }

    $logArguments = @(
        "compose", "--project-name", $projectName, "-f", $composeFile,
        "logs", "--no-color", "--tail", "100", "domain-research-mcp"
    )
    $serviceLogs = & docker @logArguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Could not read Domain Research MCP logs"
    }
    if (($serviceLogs -join "`n") -notmatch "provider_status=200") {
        throw "Tavily success marker provider_status=200 was not found in service logs"
    }

    Write-Output "Domain Research MCP smoke test passed. Containers remain running in project $projectName."
}
finally {
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], "Process")
    }
    if (Test-Path -LiteralPath $runtimeDirectory) {
        Remove-Item -LiteralPath $runtimeDirectory -Recurse -Force
    }
}
