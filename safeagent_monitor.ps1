# SafeAgent Distribution Monitor
# Searches GitHub and Reddit for duplicate execution pain cases
# Saves results to C:\SAFEAGENT\monitor\cases.json for manual review

$OutputDir = "C:\SAFEAGENT\monitor"
$CasesFile = "$OutputDir\cases.json"
$LogFile = "$OutputDir\monitor.log"

# Create output dir if needed
if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir | Out-Null
}

function Write-Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts $msg" | Tee-Object -FilePath $LogFile -Append | Write-Host
}

function Load-Cases {
    if (Test-Path $CasesFile) {
        return Get-Content $CasesFile | ConvertFrom-Json
    }
    return @()
}

function Save-Cases($cases) {
    $cases | ConvertTo-Json -Depth 5 | Set-Content $CasesFile
}

function Get-ExistingUrls($cases) {
    return $cases | ForEach-Object { $_.url }
}

# GitHub search — no auth required for public API (60 req/hour unauthenticated)
function Search-GitHub($query, $repo) {
    $encoded = [Uri]::EscapeDataString($query)
    $repoFilter = if ($repo) { "+repo:$repo" } else { "" }
    $url = "https://api.github.com/search/issues?q=$encoded$repoFilter+state:open&sort=created&order=desc&per_page=10"
    
    try {
        $headers = @{
            "User-Agent" = "SafeAgent-Monitor/1.0"
            "Accept" = "application/vnd.github.v3+json"
        }
        $response = Invoke-RestMethod -Uri $url -Headers $headers -TimeoutSec 10
        return $response.items
    } catch {
        Write-Log "GitHub search failed for '$query': $_"
        return @()
    }
}

# Reddit search
function Search-Reddit($query, $subreddit) {
    $encoded = [Uri]::EscapeDataString($query)
    $url = "https://www.reddit.com/r/$subreddit/search.json?q=$encoded&sort=new&limit=10&t=week"
    
    try {
        $headers = @{ "User-Agent" = "SafeAgent-Monitor/1.0" }
        $response = Invoke-RestMethod -Uri $url -Headers $headers -TimeoutSec 10
        return $response.data.children | ForEach-Object { $_.data }
    } catch {
        Write-Log "Reddit search failed for '$query' in r/$subreddit`: $_"
        return @()
    }
}

function Score-Pain($title, $body) {
    $highPain = @("charged twice", "double charge", "billed twice", "duplicate payment", 
                   "fired twice", "sent twice", "double email", "webhook twice", 
                   "duplicate webhook", "provisioned twice")
    $medPain  = @("idempotent", "retry", "duplicate", "at-least-once", "idempotency",
                   "crewai tool", "agent retry", "exactly once")
    
    $text = "$title $body".ToLower()
    foreach ($kw in $highPain) { if ($text -contains $kw) { return "high" } }
    foreach ($kw in $medPain)  { if ($text -contains $kw) { return "medium" } }
    return "low"
}

Write-Log "=== SafeAgent Monitor starting ==="

$existing = Load-Cases
$existingUrls = Get-ExistingUrls $existing
$newCases = @()

# --- GitHub searches ---
$githubSearches = @(
    @{ query = "duplicate tool call retry crewai";     repo = "crewAIInc/crewAI" },
    @{ query = "idempotent tool execution";            repo = "crewAIInc/crewAI" },
    @{ query = "charged twice stripe retry agent";     repo = "" },
    @{ query = "duplicate webhook stripe payment";     repo = "" },
    @{ query = "double charge retry payment agent";    repo = "" },
    @{ query = "fired twice retry tool langchain";     repo = "langchain-ai/langchain" },
    @{ query = "duplicate execution retry autogen";    repo = "microsoft/autogen" },
    @{ query = "webhook delivered twice duplicate";    repo = "pay-rails/pay" }
)

foreach ($s in $githubSearches) {
    Write-Log "GitHub: '$($s.query)' repo=$($s.repo)"
    $items = Search-GitHub $s.query $s.repo
    Start-Sleep -Milliseconds 500  # be polite to GitHub API

    foreach ($item in $items) {
        if ($existingUrls -contains $item.html_url) { continue }
        $pain = Score-Pain $item.title (if ($item.body) { $item.body } else { "" })
        if ($pain -eq "low") { continue }

        $case = [PSCustomObject]@{
            id         = [System.Guid]::NewGuid().ToString()
            source     = "github"
            category   = if ($item.html_url -match "crewAI|langchain|autogen") { "crewai" } else { "payment" }
            pain       = $pain
            status     = "pending"
            title      = $item.title
            url        = $item.html_url
            repo       = $item.repository_url -replace "https://api.github.com/repos/", ""
            snippet    = (if ($item.body) { $item.body } else { "" })[0..300] -join ""
            created_at = $item.created_at
            found_at   = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
        }
        $newCases += $case
        $existingUrls += $item.html_url
        Write-Log "  NEW [$pain] $($item.title)"
    }
}

# --- Reddit searches ---
$redditSearches = @(
    @{ query = "charged twice stripe";        sub = "SaaS" },
    @{ query = "duplicate webhook";           sub = "SaaS" },
    @{ query = "agent retry duplicate";       sub = "LocalLLaMA" },
    @{ query = "crewai tool fired twice";     sub = "LocalLLaMA" },
    @{ query = "idempotent agent payment";    sub = "Python" },
    @{ query = "double charge retry";         sub = "entrepreneur" },
    @{ query = "webhook duplicate payment";   sub = "webdev" }
)

foreach ($s in $redditSearches) {
    Write-Log "Reddit: '$($s.query)' r/$($s.sub)"
    $posts = Search-Reddit $s.query $s.sub
    Start-Sleep -Milliseconds 500

    foreach ($post in $posts) {
        $postUrl = "https://reddit.com$($post.permalink)"
        if ($existingUrls -contains $postUrl) { continue }
        $pain = Score-Pain $post.title (if ($post.selftext) { $post.selftext } else { "" })
        if ($pain -eq "low") { continue }

        $case = [PSCustomObject]@{
            id         = [System.Guid]::NewGuid().ToString()
            source     = "reddit"
            category   = "saas"
            pain       = $pain
            status     = "pending"
            title      = $post.title
            url        = $postUrl
            repo       = "r/$($s.sub)"
            snippet    = (if ($post.selftext) { $post.selftext } else { "" })[0..300] -join ""
            created_at = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
            found_at   = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
        }
        $newCases += $case
        $existingUrls += $postUrl
        Write-Log "  NEW [$pain] $($post.title)"
    }
}

# Merge and save
$allCases = @($existing) + @($newCases)
Save-Cases $allCases

$pending = ($allCases | Where-Object { $_.status -eq "pending" }).Count
Write-Log "Done. $($newCases.Count) new cases. $pending total pending. Cases saved to $CasesFile"

# Print summary to console
Write-Host ""
Write-Host "=== PENDING CASES FOR REVIEW ===" -ForegroundColor Cyan
$allCases | Where-Object { $_.status -eq "pending" } | Sort-Object pain -Descending | ForEach-Object {
    $color = if ($_.pain -eq "high") { "Red" } else { "Yellow" }
    Write-Host "[$($_.pain.ToUpper())] $($_.title)" -ForegroundColor $color
    Write-Host "  $($_.url)" -ForegroundColor Gray
    Write-Host ""
}
