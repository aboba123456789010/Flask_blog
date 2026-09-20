"""Тесты для функций отправки email в app.py.

Все тесты работают без реального сетевого доступа и без настоящих
учётных данных — smtplib.SMTP подменяется моком, поэтому их безопасно
запускать в CI (GitHub Actions).
"""
from unittest.mock import patch, MagicMock

import app as app_module


def test_send_welcome_email_missing_credentials(monkeypatch):
    monkeypatch.delenv('EMAIL_USER', raising=False)
    monkeypatch.delenv('EMAIL_PASSWORD', raising=False)

    with patch('app.smtplib.SMTP') as smtp:
        result = app_module.send_welcome_email('user@example.com', 'User')

    assert result is False
    smtp.assert_not_called()


def test_send_welcome_email_success(monkeypatch):
    monkeypatch.setenv('EMAIL_USER', 'bot@example.com')
    monkeypatch.setenv('EMAIL_PASSWORD', 'app-password')

    mock_server = MagicMock()
    with patch('app.smtplib.SMTP', return_value=mock_server) as smtp:
        result = app_module.send_welcome_email('user@example.com', 'User')

    assert result is True
    smtp.assert_called_once_with('smtp.gmail.com', 587)
    mock_server.starttls.assert_called_once()
    mock_server.login.assert_called_once_with('bot@example.com', 'app-password')
    mock_server.send_message.assert_called_once()


def test_send_error_email_missing_credentials(monkeypatch):
    monkeypatch.delenv('EMAIL_USER', raising=False)
    monkeypatch.delenv('EMAIL_PASSWORD', raising=False)
    monkeypatch.delenv('ADMIN_EMAIL', raising=False)

    with patch('app.smtplib.SMTP') as smtp:
        result = app_module.send_error_email('Subject', 'Body')

    assert result is False
    smtp.assert_not_called()
