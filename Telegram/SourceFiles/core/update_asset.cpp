/*
This file is part of Telegram Desktop,
the official desktop application for the Telegram messaging service.

For license and copyright information please follow this link:
https://github.com/telegramdesktop/tdesktop/blob/master/LEGAL
*/
#include "core/update_asset.h"

#include "core/core_settings.h"
#include "core/update_checker.h"

#include <QtCore/QCoreApplication>
#include <QtCore/QDir>
#include <QtCore/QFile>
#include <QtCore/QUrl>
#include <QtGui/QDesktopServices>
#include <QtNetwork/QNetworkAccessManager>
#include <QtNetwork/QNetworkReply>
#include <QtNetwork/QNetworkRequest>

#ifdef Q_OS_WIN
#include <QProcess>
#endif

namespace Core {
namespace {

constexpr auto kReplaceWaitSeconds = 4;

[[nodiscard]] QString DownloadDir() {
	return cWorkingDir() + u"tupdates/"_q;
}

} // namespace

UpdateAssetDownload::UpdateAssetDownload() = default;

void UpdateAssetDownload::start(
		const QUrl &url,
		const QString &fileName) {
	cancel();
	const auto dir = DownloadDir();
	if (!QDir().mkpath(dir)) {
		_failed.fire({});
		return;
	}
	_path = dir + fileName;
	_file = std::make_unique<QFile>(_path);
	if (!_file->open(QIODevice::WriteOnly | QIODevice::Truncate)) {
		cleanup();
		_failed.fire({});
		return;
	}
	auto request = QNetworkRequest(url);
	request.setAttribute(
		QNetworkRequest::RedirectPolicyAttribute,
		QNetworkRequest::NoLessSafeRedirectPolicy);
	request.setAttribute(
		QNetworkRequest::Http2AllowedAttribute,
		false);
	request.setRawHeader("User-Agent", "AyuGram-Update-Checker");
	_manager = std::make_unique<QNetworkAccessManager>();
	_reply = _manager->get(request);
	QObject::connect(
		_reply,
		&QNetworkReply::downloadProgress,
		_reply,
		[=](qint64 done, qint64 total) {
			_progress.fire({
				int64(done),
				int64(total),
				false,
			});
		});
	QObject::connect(
		_reply,
		&QNetworkReply::readyRead,
		_reply,
		[=] { _file->write(_reply->readAll()); });
	QObject::connect(
		_reply,
		&QNetworkReply::finished,
		_reply,
		[=] {
			const auto error = _reply->error();
			const auto status = _reply->attribute(
				QNetworkRequest::HttpStatusCodeAttribute).toInt();
			const auto ok = (error == QNetworkReply::NoError)
				&& (status == 200);
			if (!ok) {
				cleanup();
				_failed.fire({});
				return;
			}
			_file->write(_reply->readAll());
			_file->close();
			auto path = _path;
			_path = QString();
			_file.reset();
			if (const auto reply = base::take(_reply)) {
				reply->deleteLater();
			}
			_manager.reset();
			_ready.fire(std::move(path));
		});
}


void UpdateAssetDownload::cancel() {
	if (!_reply) {
		return;
	}
	_reply->abort();
	cleanup();
}

void UpdateAssetDownload::cleanup() {
	if (const auto reply = base::take(_reply)) {
		reply->deleteLater();
	}
	_manager.reset();
	_file.reset();
	if (!_path.isEmpty()) {
		QFile::remove(_path);
		_path = QString();
	}
}

rpl::producer<UpdateAssetDownload::Progress> UpdateAssetDownload::progress() const {
	return _progress.events();
}

rpl::producer<QString> UpdateAssetDownload::readyFile() const {
	return _ready.events();
}

rpl::producer<> UpdateAssetDownload::failed() const {
	return _failed.events();
}

bool UpdateAssetDownload::active() const {
	return (_reply != nullptr);
}

// WHY: minizip's fopen ioapi cannot reach non-ANSI paths on Windows, so
// unpacking runs through cmd built-ins with one PowerShell call.
bool InstallDownloadedAsset(const QString &path) {
#ifdef Q_OS_MAC
	return QDesktopServices::openUrl(QUrl::fromLocalFile(path));
#elif defined(Q_OS_WIN) // Q_OS_MAC
	const auto dir = QCoreApplication::applicationDirPath();
	const auto zip = dir + u"/ayu_update.zip"_q;
	QFile::remove(zip);
	if (!QFile::copy(path, zip)) {
		return false;
	}
	const auto bat = dir + u"/ayu_update_install.bat"_q;
	auto f = QFile(bat);
	if (!f.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
		QFile::remove(zip);
		return false;
	}
	auto script = u"ping -n 5 127.0.0.1 > nul\r\n"_q;
	script += u"powershell -NoProfile -Command \"Expand-Archive -LiteralPath '%~dp0ayu_update.zip' -DestinationPath '%~dp0ayu_update_tmp' -Force\"\r\n"_q;
	script += u"move /y \"%~dp0AyuGram.exe\" \"%~dp0AyuGram.exe.old\"\r\n"_q;
	script += u"move /y \"%~dp0ayu_update_tmp\\AyuGram.exe\" \"%~dp0AyuGram.exe\"\r\n"_q;
	script += u"rmdir /s /q \"%~dp0ayu_update_tmp\"\r\n"_q;
	script += u"del \"%~dp0ayu_update.zip\"\r\n"_q;
	script += u"start \"\" \"%~dp0AyuGram.exe\"\r\n"_q;
	script += u"del \"%~f0\"\r\n"_q;
	f.write(script.toUtf8());
	f.close();
	return QProcess::startDetached(u"cmd"_q, { u"/c"_q, bat });
#else // Q_OS_WIN
	return false;
#endif // Q_OS_WIN
}

} // namespace Core
