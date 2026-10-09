import Foundation
import Vision
import ImageIO

let url = URL(fileURLWithPath: CommandLine.arguments[1])
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false
request.recognitionLanguages = ["en-US"]
request.customWords = ["VEHIS"]
try VNImageRequestHandler(url: url).perform([request])
let rows: [[String: Any]] = (request.results ?? []).compactMap { result in
    guard let text = result.topCandidates(1).first else { return nil }
    let box = result.boundingBox
    return ["text": text.string, "confidence": text.confidence,
            "box": [box.minX, 1 - box.maxY, box.width, box.height]]
}
let data = try JSONSerialization.data(withJSONObject: rows)
print(String(data: data, encoding: .utf8)!)
