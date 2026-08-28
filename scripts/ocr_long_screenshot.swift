import AppKit
import Foundation
import Vision

guard CommandLine.arguments.count == 3 else {
    fputs("usage: ocr_long_screenshot.swift INPUT OUTPUT\n", stderr)
    exit(2)
}

let input = URL(fileURLWithPath: CommandLine.arguments[1])
let output = URL(fileURLWithPath: CommandLine.arguments[2])
guard let image = NSImage(contentsOf: input),
      let data = image.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: data),
      let cgImage = bitmap.cgImage else {
    fputs("cannot load input image\n", stderr)
    exit(3)
}

let stripHeight = 3200
let overlap = 100
var y = 0
var stripIndex = 0
var sections: [String] = []

while y < cgImage.height {
    let height = min(stripHeight, cgImage.height - y)
    let rect = CGRect(x: 0, y: y, width: cgImage.width, height: height)
    guard let crop = cgImage.cropping(to: rect) else { break }

    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    request.usesLanguageCorrection = true
    let handler = VNImageRequestHandler(cgImage: crop, orientation: .up)
    try handler.perform([request])

    let observations = (request.results ?? []).sorted {
        if abs($0.boundingBox.midY - $1.boundingBox.midY) > 0.01 {
            return $0.boundingBox.midY > $1.boundingBox.midY
        }
        return $0.boundingBox.minX < $1.boundingBox.minX
    }
    let lines = observations.compactMap { $0.topCandidates(1).first?.string }
    sections.append("\n--- OCR STRIP \(stripIndex) y=\(y) ---\n" + lines.joined(separator: "\n"))

    stripIndex += 1
    if y + height >= cgImage.height { break }
    y += stripHeight - overlap
}

try sections.joined(separator: "\n").write(to: output, atomically: true, encoding: .utf8)
print("ocr_complete strips=\(stripIndex) output=\(output.path)")
