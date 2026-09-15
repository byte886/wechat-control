#!/usr/bin/env bash
set -euo pipefail

json_mode=0
region_left=""
region_bottom=""
region_width=""
region_height=""

while [ "$#" -gt 0 ]; do
  case "${1:-}" in
    --json)
      json_mode=1
      shift
      ;;
    --region)
      if [ "$#" -lt 5 ]; then
        echo "Usage: $0 [--json] [--region left bottom width height] <image.png>" >&2
        exit 1
      fi
      region_left="$2"
      region_bottom="$3"
      region_width="$4"
      region_height="$5"
      shift 5
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
    *)
      break
      ;;
  esac
done

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 [--json] [--region left bottom width height] <image.png>" >&2
  exit 1
fi

image_path="$1"

if [ ! -f "$image_path" ]; then
  echo "Image not found: $image_path" >&2
  exit 1
fi

for value in "$region_left" "$region_bottom" "$region_width" "$region_height"; do
  if [ -n "$value" ] && ! printf '%s' "$value" | grep -Eq '^[0-9]+([.][0-9]+)?$'; then
    echo "region values must be numeric between 0 and 1" >&2
    exit 1
  fi
done

if [ -n "$region_left" ] && [ -z "$region_height" ]; then
  echo "region requires left bottom width height" >&2
  exit 1
fi

osascript -l JavaScript - "$image_path" "$json_mode" "${region_left:-}" "${region_bottom:-}" "${region_width:-}" "${region_height:-}" <<'JXA'
ObjC.import('Foundation')
ObjC.import('Vision')
ObjC.import('CoreImage')

function toNumber(value) {
  return Number(ObjC.unwrap(value))
}

function writeStdout(text) {
  const payload = $(String(text)).dataUsingEncoding($.NSUTF8StringEncoding)
  $.NSFileHandle.fileHandleWithStandardOutput.writeData(payload)
}

function scriptArgs() {
  const args = $.NSProcessInfo.processInfo.arguments
  const values = []
  for (let i = 0; i < Number(args.count); i++) {
    values.push(ObjC.unwrap(args.objectAtIndex(i)))
  }
  return values.slice(4)
}

function main(argv) {
  const imagePath = argv[0]
  const jsonMode = argv[1] === '1'
  const hasRegion = argv[2] !== ''
  const region = hasRegion ? {
    left: Number(argv[2]),
    bottom: Number(argv[3]),
    width: Number(argv[4]),
    height: Number(argv[5])
  } : null
  const imageURL = $.NSURL.fileURLWithPath(imagePath)
  const ciImage = $.CIImage.imageWithContentsOfURL(imageURL)

  if (!ciImage || ObjC.unwrap(ciImage.isNull ? ciImage.isNull() : false)) {
    throw new Error('Could not load image for OCR: ' + imagePath)
  }

  const handler = $.VNImageRequestHandler.alloc.initWithCIImageOptions(ciImage, $({}))
  const request = $.VNRecognizeTextRequest.alloc.init
  request.recognitionLevel = $.VNRequestTextRecognitionLevelAccurate
  request.usesLanguageCorrection = true
  request.recognitionLanguages = $(['zh-Hans', 'en-US'])

  const ok = handler.performRequestsError($.NSArray.arrayWithObject(request), null)
  if (!ok) {
    throw new Error('Vision OCR failed for image: ' + imagePath)
  }

  const items = []
  const results = request.results
  const count = results ? Number(results.count) : 0

  for (let i = 0; i < count; i++) {
    const observation = results.objectAtIndex(i)
    const candidates = observation.topCandidates(1)
    if (!candidates || Number(candidates.count) === 0) {
      continue
    }

    const candidate = candidates.objectAtIndex(0)
    const text = ObjC.unwrap(candidate.string).trim()
    if (!text) {
      continue
    }

    const box = observation.boundingBox
    const item = {
      text,
      x: toNumber(box.origin.x),
      y: toNumber(box.origin.y),
      w: toNumber(box.size.width),
      h: toNumber(box.size.height)
    }

    if (region) {
      const centerX = item.x + item.w / 2
      const centerY = item.y + item.h / 2
      const withinX = centerX >= region.left && centerX <= region.left + region.width
      const withinY = centerY >= region.bottom && centerY <= region.bottom + region.height
      if (!withinX || !withinY) {
        continue
      }
    }

    items.push({
      text: item.text,
      x: item.x,
      y: item.y,
      w: item.w,
      h: item.h
    })
  }

  items.sort((left, right) => {
    const yDelta = right.y - left.y
    if (Math.abs(yDelta) > 0.003) {
      return yDelta
    }
    return left.x - right.x
  })

  if (jsonMode) {
    writeStdout(JSON.stringify(items, null, 2) + '\n')
    return
  }

  for (const item of items) {
    writeStdout(item.text + '\n')
  }
}

main(scriptArgs())
JXA
